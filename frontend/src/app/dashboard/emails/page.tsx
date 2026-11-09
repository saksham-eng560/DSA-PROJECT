"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import useSWR from "swr";
import { CheckCircle2, Inbox, Mail, RefreshCw, Reply, Send } from "lucide-react";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { useIntegrations } from "@/hooks/use-applications";
import { ApiError, fetcher, patch, post } from "@/lib/api-client";
import type { Communication, Paginated } from "@/lib/types";
import { cn, formatDateTime, timeAgo, titleCase } from "@/lib/utils";

const INTENT_TONE: Record<string, "success" | "danger" | "warning" | "info" | "muted" | "primary"> = {
  offer: "success", interview_invite: "success", assessment: "primary", rejection: "danger",
  info_request: "warning", follow_up: "info", acknowledgment: "muted", generic: "muted",
};

function EmailDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { data: email, mutate } = useSWR<Communication>(`/communications/${id}`, fetcher);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const toast = useToast();
  useEffect(() => setReply(email?.suggested_reply || ""), [email?.id, email?.suggested_reply]);
  if (!email) return null;

  const act = async (kind: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(kind);
    try {
      await fn();
      toast({ title: ok, tone: "success" });
      mutate();
      onChanged();
    } catch (err) {
      toast({ title: "Failed", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setBusy(null);
    }
  };
  const details = Object.entries(email.extracted_details || {}).filter(([, v]) => v !== "" && v !== 0 && v != null);

  return (
    <Card className="sticky top-20">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          {email.detected_intent && <Badge tone={INTENT_TONE[email.detected_intent] || "muted"}>{titleCase(email.detected_intent)}</Badge>}
          {email.urgency && <Badge tone="outline">{email.urgency} urgency</Badge>}
          {email.intent_confidence != null && <Badge tone="muted">{Math.round(email.intent_confidence * 100)}% confident</Badge>}
        </div>
        <CardTitle className="mt-2 text-lg">{email.subject}</CardTitle>
        <p className="text-sm text-muted-foreground">{email.sender_name} &lt;{email.sender_email}&gt; · {formatDateTime(email.received_at)}</p>
        {email.application && (
          <Link href={`/dashboard/applications/${email.application.id}`} className="text-sm text-primary hover:underline">
            {email.application.role_title} @ {email.application.company_name} →
          </Link>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {!!details.length && (
          <div className="grid grid-cols-2 gap-2 bg-muted/50 p-3 text-sm">
            {details.map(([k, v]) => (
              <div key={k}><p className="text-xs text-muted-foreground">{titleCase(k)}</p><p className="break-words font-medium">{String(v)}</p></div>
            ))}
          </div>
        )}
        <div className="prose-pre max-h-72 overflow-y-auto border p-3 text-muted-foreground">{email.body_text}</div>
        <div>
          <p className="mb-1.5 flex items-center gap-1.5 text-sm font-medium"><Reply className="h-4 w-4" /> Suggested reply</p>
          <Textarea value={reply} onChange={(e) => setReply(e.target.value)} className="min-h-[140px]" placeholder="No reply needed for this e-mail." />
          <div className="mt-2 flex flex-wrap gap-2">
            <Button variant="outline" size="sm" disabled={!reply.trim()} loading={busy === "draft"}
              onClick={() => act("draft", () => post(`/communications/${email.id}/draft`, { body: reply }), "Draft saved to Gmail")}>
              <Mail /> Save as Gmail draft
            </Button>
            <Button size="sm" disabled={!reply.trim()} loading={busy === "send"}
              onClick={() => confirm("Send this reply from your Gmail now?") && act("send", () => post(`/communications/${email.id}/send`, { body: reply }), "Reply sent")}>
              <Send /> Send reply
            </Button>
            <Button variant="ghost" size="sm" loading={busy === "done"}
              onClick={() => act("done", () => patch(`/communications/${email.id}`, { action_taken: !email.action_taken }), email.action_taken ? "Marked as open" : "Marked as done")}>
              <CheckCircle2 /> {email.action_taken ? "Reopen" : "Mark done"}
            </Button>
          </div>
          {email.gmail_draft_id && <p className="mt-2 text-xs text-muted-foreground">A draft reply is waiting in your Gmail drafts.</p>}
        </div>
      </CardContent>
    </Card>
  );
}

function EmailsInner() {
  const params = useSearchParams();
  const [selected, setSelected] = useState<string | null>(params.get("id"));
  const [intent, setIntent] = useState("");
  const [actionOnly, setActionOnly] = useState(false);
  const { data: integrations } = useIntegrations();
  const toast = useToast();
  const query = new URLSearchParams({ page_size: "50" });
  if (intent) query.set("intent", intent);
  if (actionOnly) query.set("action_required", "true");
  const { data, mutate } = useSWR<Paginated<Communication>>(`/communications?${query}`, fetcher, { refreshInterval: 30000 });

  const checkNow = async () => {
    try {
      await post("/agent/check-email");
      toast({ title: "Checking Gmail…", description: "New recruiter e-mails will appear shortly.", tone: "success" });
      setTimeout(() => mutate(), 5000);
    } catch (err) {
      toast({ title: "Could not check e-mail", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    }
  };

  return (
    <div>
      <PageHeader title="Recruiter emails"
        description={integrations?.google.gmail ? `Monitoring ${integrations.google.email}${integrations.google.last_polled_at ? ` · checked ${timeAgo(integrations.google.last_polled_at)}` : ""}` : "Connect Gmail in Settings to monitor recruiter replies."}
        actions={integrations?.google.gmail && <Button variant="outline" onClick={checkNow}><RefreshCw /> Check now</Button>} />
      <div className="mb-4 flex flex-wrap gap-2">
        <Select className="w-52" aria-label="Filter by intent" value={intent} onChange={(e) => setIntent(e.target.value)}>
          <option value="">All intents</option>
          {["interview_invite", "offer", "assessment", "info_request", "follow_up", "rejection", "acknowledgment", "generic"].map((i) => <option key={i} value={i}>{titleCase(i)}</option>)}
        </Select>
        <Button variant={actionOnly ? "default" : "outline"} onClick={() => setActionOnly((a) => !a)}>Action required</Button>
      </div>
      {!data?.items.length ? (
        <EmptyState icon={Inbox} title="No recruiter e-mails yet"
          description={integrations?.google.gmail ? "Replies from companies you applied to will show up here automatically." : "Connect your Google account under Settings → Integrations."} />
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <div className="space-y-2">
            {data.items.map((c) => (
              <button key={c.id} onClick={() => setSelected(c.id)}
                className={cn("block w-full border bg-card p-3 text-left transition-colors hover:bg-accent/50", selected === c.id && "border-primary ring-1 ring-primary")}>
                <div className="flex items-center justify-between gap-2">
                  <p className={cn("truncate text-sm", c.is_action_required && !c.action_taken ? "font-semibold" : "font-medium")}>{c.sender_name || c.sender_email}</p>
                  <span className="shrink-0 text-xs text-muted-foreground">{timeAgo(c.received_at)}</span>
                </div>
                <p className="truncate text-sm">{c.subject}</p>
                <div className="mt-1 flex items-center gap-2">
                  {c.detected_intent && <Badge tone={INTENT_TONE[c.detected_intent] || "muted"}>{titleCase(c.detected_intent)}</Badge>}
                  {c.is_action_required && !c.action_taken && <Badge tone="warning">Action</Badge>}
                  <span className="truncate text-xs text-muted-foreground">{c.snippet}</span>
                </div>
              </button>
            ))}
          </div>
          <div>{selected ? <EmailDetail id={selected} onChanged={() => mutate()} /> : <EmptyState icon={Mail} title="Select an e-mail" />}</div>
        </div>
      )}
    </div>
  );
}

export default function EmailsPage() {
  return <Suspense><EmailsInner /></Suspense>;
}
