"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import useSWR from "swr";
import { CalendarDays, CalendarPlus, ExternalLink, MapPin, Plus, RefreshCw, Video } from "lucide-react";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Modal } from "@/components/modal";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { useApplications } from "@/hooks/use-applications";
import { ApiError, fetcher, patch, post } from "@/lib/api-client";
import type { Interview } from "@/lib/types";
import { cn, formatDateTime, titleCase } from "@/lib/utils";

const TYPES = ["phone_screen", "video_call", "technical", "behavioral", "panel", "onsite", "take_home", "pair_programming", "other"];

function AddInterview({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (id: string) => void }) {
  const { data: apps } = useApplications({ status: "applied,acknowledged,screening,interview,assessment,final_round", page_size: 100 });
  const [form, setForm] = useState({ application_id: "", scheduled_at: "", duration_minutes: 60, interview_type: "video_call", meeting_link: "", physical_location: "" });
  const [loading, setLoading] = useState(false);
  const toast = useToast();
  const submit = async () => {
    setLoading(true);
    try {
      const created = await post<Interview>("/interviews", {
        ...form,
        scheduled_at: new Date(form.scheduled_at).toISOString(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        meeting_link: form.meeting_link || null,
        physical_location: form.physical_location || null,
      });
      toast({ title: "Interview added", description: "Prep notes generated and synced to your calendar.", tone: "success" });
      onOpenChange(false);
      onCreated(created.id);
    } catch (err) {
      toast({ title: "Could not add interview", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setLoading(false);
    }
  };
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Add an interview" description="Creates a calendar event with AI prep notes."
      footer={<><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button onClick={submit} loading={loading} disabled={!form.application_id || !form.scheduled_at}>Add interview</Button></>}>
      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="iv-application">Application</Label>
          <Select id="iv-application" value={form.application_id} onChange={(e) => setForm({ ...form, application_id: e.target.value })}>
            <option value="">Select…</option>
            {apps?.items.map((a) => <option key={a.id} value={a.id}>{a.job?.company_name} — {a.job?.role_title}</option>)}
          </Select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5"><Label htmlFor="iv-when">Date & time</Label><Input id="iv-when" type="datetime-local" value={form.scheduled_at} onChange={(e) => setForm({ ...form, scheduled_at: e.target.value })} /></div>
          <div className="space-y-1.5"><Label htmlFor="iv-duration">Duration (min)</Label><Input id="iv-duration" type="number" min={5} value={form.duration_minutes} onChange={(e) => setForm({ ...form, duration_minutes: Number(e.target.value) })} /></div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="iv-type">Type</Label>
          <Select id="iv-type" value={form.interview_type} onChange={(e) => setForm({ ...form, interview_type: e.target.value })}>
            {TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
          </Select>
        </div>
        <div className="space-y-1.5"><Label htmlFor="iv-link">Meeting link</Label><Input id="iv-link" placeholder="https://zoom.us/j/…" value={form.meeting_link} onChange={(e) => setForm({ ...form, meeting_link: e.target.value })} /></div>
        <div className="space-y-1.5"><Label htmlFor="iv-location">Location (on-site)</Label><Input id="iv-location" value={form.physical_location} onChange={(e) => setForm({ ...form, physical_location: e.target.value })} /></div>
      </div>
    </Modal>
  );
}

function InterviewDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { data: interview, mutate } = useSWR<Interview>(`/interviews/${id}`, fetcher);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const toast = useToast();
  useEffect(() => setFeedback(interview?.feedback || ""), [interview?.id, interview?.feedback]);
  if (!interview) return null;
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
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="primary">{titleCase(interview.interview_type || "interview")}</Badge>
          {interview.outcome && <Badge tone={interview.outcome === "passed" ? "success" : interview.outcome === "failed" ? "danger" : "muted"}>{titleCase(interview.outcome)}</Badge>}
        </div>
        <CardTitle className="mt-2 text-lg">{interview.role_title} @ {interview.company_name}</CardTitle>
        <CardDescription>{formatDateTime(interview.scheduled_at)} · {interview.duration_minutes} min · {interview.timezone}</CardDescription>
        <div className="flex flex-wrap gap-2 pt-2">
          {interview.meeting_link && <a href={interview.meeting_link} target="_blank" rel="noreferrer" className={buttonVariants({ size: "sm" })}><Video /> Join {titleCase(interview.meeting_platform || "")}</a>}
          {interview.physical_location && <span className="inline-flex items-center gap-1 text-sm text-muted-foreground"><MapPin className="h-4 w-4" />{interview.physical_location}</span>}
          {interview.google_event_link && <a href={interview.google_event_link} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", size: "sm" })}><CalendarDays /> Calendar <ExternalLink /></a>}
          <Button size="sm" variant="ghost" loading={busy === "prep"} onClick={() => act("prep", () => post(`/interviews/${interview.id}/prep`), "Prep notes regenerated")}><RefreshCw /> Regenerate prep</Button>
        </div>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="prep">
          <TabsList>
            <TabsTrigger value="prep">Prep notes</TabsTrigger>
            <TabsTrigger value="questions">Likely questions</TabsTrigger>
            <TabsTrigger value="company">Company</TabsTrigger>
            <TabsTrigger value="outcome">Outcome</TabsTrigger>
          </TabsList>
          <TabsContent value="prep"><div className="prose-pre bg-muted/40 p-4">{interview.prep_notes || "No prep notes yet."}</div></TabsContent>
          <TabsContent value="questions">
            <ol className="space-y-3">
              {(interview.likely_questions || []).map((q, i) => (
                <li key={i} className="border p-3">
                  <p className="text-sm font-medium">{i + 1}. {q.question}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{q.answer_outline}</p>
                </li>
              ))}
            </ol>
          </TabsContent>
          <TabsContent value="company"><div className="prose-pre text-muted-foreground">{interview.company_research || "—"}</div></TabsContent>
          <TabsContent value="outcome">
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2">
                {["pending", "passed", "failed", "rescheduled", "cancelled"].map((o) => (
                  <Button key={o} size="sm" variant={interview.outcome === o ? "default" : "outline"} loading={busy === o}
                    onClick={() => act(o, () => patch(`/interviews/${interview.id}`, { outcome: o }), "Outcome saved")}>{titleCase(o)}</Button>
                ))}
              </div>
              <Textarea placeholder="How did it go? What were you asked?" value={feedback} onChange={(e) => setFeedback(e.target.value)} className="min-h-[140px]" />
              <Button size="sm" loading={busy === "feedback"} onClick={() => act("feedback", () => patch(`/interviews/${interview.id}`, { feedback }), "Debrief saved")}>Save debrief</Button>
            </div>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}

function InterviewsInner() {
  const params = useSearchParams();
  const [selected, setSelected] = useState<string | null>(params.get("id"));
  const [addOpen, setAddOpen] = useState(false);
  const { data: upcoming, mutate: m1 } = useSWR<{ items: Interview[] }>("/interviews?upcoming=true", fetcher);
  const { data: past, mutate: m2 } = useSWR<{ items: Interview[] }>("/interviews?upcoming=false", fetcher);
  const refresh = () => { m1(); m2(); };
  useEffect(() => {
    if (!selected && upcoming?.items.length) setSelected(upcoming.items[0].id);
  }, [upcoming, selected]);

  const renderList = (items: Interview[]) => items.map((i) => (
    <button key={i.id} onClick={() => setSelected(i.id)}
      className={cn("block w-full border bg-card p-3 text-left hover:bg-accent/50", selected === i.id && "border-primary ring-1 ring-primary")}>
      <p className="font-medium">{i.company_name}</p>
      <p className="text-sm text-muted-foreground">{i.role_title}</p>
      <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(i.scheduled_at)} · {titleCase(i.interview_type || "interview")}</p>
    </button>
  ));

  return (
    <div>
      <PageHeader title="Interviews" description="Auto-created from recruiter e-mails, synced to Google Calendar with 24h and 1h reminders."
        actions={<Button onClick={() => setAddOpen(true)}><Plus /> Add interview</Button>} />
      {!upcoming?.items.length && !past?.items.length ? (
        <EmptyState icon={CalendarPlus} title="No interviews yet" description="When a recruiter invites you, the agent adds it here and to your calendar with prep notes." />
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
          <div className="space-y-4">
            <div><p className="label-caps mb-2 text-[10px] text-muted-foreground">Upcoming</p><div className="space-y-2">{upcoming?.items.length ? renderList(upcoming.items) : <p className="text-sm text-muted-foreground">None scheduled.</p>}</div></div>
            {!!past?.items.length && <div><p className="label-caps mb-2 text-[10px] text-muted-foreground">Past</p><div className="space-y-2">{renderList(past.items)}</div></div>}
          </div>
          <div>{selected && <InterviewDetail id={selected} onChanged={refresh} />}</div>
        </div>
      )}
      <AddInterview open={addOpen} onOpenChange={setAddOpen} onCreated={(id) => { refresh(); setSelected(id); }} />
    </div>
  );
}

export default function InterviewsPage() {
  return <Suspense><InterviewsInner /></Suspense>;
}
