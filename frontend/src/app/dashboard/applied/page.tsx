"use client";

import Link from "next/link";
import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  ArrowUpRight, BellRing, Building2, CalendarDays, CheckCheck, Clock3, ExternalLink, Mail, MapPin, Plus, Search,
} from "lucide-react";
import { EmptyState } from "@/components/empty-state";
import { AnimatedNumber, EASE_OUT, Stagger, StaggerItem } from "@/components/motion";
import { Modal } from "@/components/modal";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { useApplications, useMe } from "@/hooks/use-applications";
import { useRefreshTracking } from "@/components/i-applied-button";
import { ApiError, post } from "@/lib/api-client";
import type { ApplicationDetail, ApplicationStatus, ApplicationSummary } from "@/lib/types";
import { STATUS_LABELS, cn, formatDate } from "@/lib/utils";

/** Where an application is on the road from "applied" to "offer". */
const STAGES = [
  { key: "applied", label: "Applied", short: "Applied" },
  { key: "heard", label: "Heard back", short: "Replied" },
  { key: "process", label: "Interviewing", short: "Interview" },
  { key: "offer", label: "Offer", short: "Offer" },
] as const;
const HEARD: ApplicationStatus[] = ["acknowledged", "screening", "assessment", "interview", "final_round", "offer", "accepted", "rejected"];
const PROCESS: ApplicationStatus[] = ["screening", "assessment", "interview", "final_round", "offer", "accepted"];
const OFFER: ApplicationStatus[] = ["offer", "accepted"];
const CLOSED: ApplicationStatus[] = ["rejected", "withdrawn"];
const FOLLOW_UP_DAYS = 7;

const FILTERS: { key: string; label: string; statuses?: string }[] = [
  { key: "all", label: "All" },
  { key: "waiting", label: "Waiting for a reply", statuses: "applied,acknowledged" },
  { key: "process", label: "In process", statuses: "screening,assessment,interview,final_round" },
  { key: "offers", label: "Offers", statuses: "offer,accepted" },
  { key: "closed", label: "Closed", statuses: "rejected,withdrawn" },
];
const UPDATABLE: ApplicationStatus[] = ["acknowledged", "screening", "assessment", "interview", "final_round", "offer", "accepted", "rejected", "withdrawn"];

function stageIndex(status: ApplicationStatus) {
  if (OFFER.includes(status)) return 3;
  if (PROCESS.includes(status)) return 2;
  if (HEARD.includes(status)) return 1;
  return 0;
}

function daysSince(value: string | null) {
  if (!value) return null;
  return Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 86_400_000));
}

// ------------------------------------------------------------------ headline tiles
function StageTile({ label, value, total, index }: { label: string; value: number; total: number; index: number }) {
  const share = total ? Math.round((value / total) * 100) : 0;
  const reduce = useReducedMotion();
  return (
    <div className="flex flex-col bg-card p-5">
      <div className="flex items-center justify-between text-muted-foreground">
        <span className="text-xs font-medium">{label}</span>
        <span className="font-mono text-[10px]">0{index + 1}</span>
      </div>
      <p className="mt-4 font-display text-4xl leading-none"><AnimatedNumber value={value} /></p>
      <div className="mt-4" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={share} aria-label={`${label}: ${share}% of your applications`}>
        <div className="h-1.5 bg-primary/15">
          <motion.div className="h-full origin-left bg-primary" initial={reduce ? false : { scaleX: 0 }} animate={{ scaleX: share / 100 }}
            transition={{ duration: 0.9, delay: 0.15 + index * 0.08, ease: EASE_OUT }} />
        </div>
        <p className="mt-2 text-xs text-muted-foreground">{index === 0 ? "everything you applied to" : `${share}% of your applications`}</p>
      </div>
    </div>
  );
}

/** Motion graphic: a signal pulse travels the tracking line while the agent watches your inbox. */
function TrackingLine() {
  const reduce = useReducedMotion();
  return (
    <div className="relative hidden h-10 overflow-hidden px-2 md:block" aria-hidden>
      <div className="relative h-full">
      <div className="absolute inset-x-0 top-1/2 h-px bg-line/70" />
      {STAGES.map((s, i) => (
        <span key={s.key} className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 border border-foreground/60 bg-background"
          style={{ left: `${(i / (STAGES.length - 1)) * 100}%` }} />
      ))}
      {!reduce && (
        <motion.span className="absolute top-1/2 h-[3px] w-24 -translate-y-1/2 bg-gradient-to-r from-transparent via-primary to-primary"
          initial={{ left: "-10%" }} animate={{ left: ["-10%", "100%"] }}
          transition={{ duration: 3.2, repeat: Infinity, ease: "easeInOut", repeatDelay: 0.6 }} />
      )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ one tracked application
function ProgressRail({ status }: { status: ApplicationStatus }) {
  const reduce = useReducedMotion();
  const closed = CLOSED.includes(status);
  const reached = stageIndex(status);
  return (
    <div className="mt-4" aria-label={`Progress: ${STATUS_LABELS[status]}`}>
      <div className="relative h-1 bg-foreground/10">
        <motion.div className={cn("absolute inset-y-0 left-0 origin-left", closed ? "bg-muted-foreground/60" : "bg-primary")}
          style={{ width: "100%" }} initial={reduce ? false : { scaleX: 0 }} animate={{ scaleX: (reached + (closed ? 0.5 : 1)) / STAGES.length }}
          transition={{ duration: 0.8, ease: EASE_OUT, delay: 0.2 }} />
      </div>
      <ol className="mt-2 grid grid-cols-4 gap-2 text-[10px] uppercase tracking-wider">
        {STAGES.map((s, i) => (
          <li key={s.key} className={cn("truncate", i <= reached && !closed ? "text-foreground" : "text-muted-foreground")}>{s.short}</li>
        ))}
      </ol>
    </div>
  );
}

function TrackedCard({ app, onChanged }: { app: ApplicationSummary; onChanged: () => void }) {
  const toast = useToast();
  const job = app.job;
  const waited = daysSince(app.submitted_at);
  const waiting = app.status === "applied" || app.status === "acknowledged";
  const nudge = waiting && waited != null && waited >= FOLLOW_UP_DAYS;
  const link = job?.application_url || job?.source_url;

  const updateStatus = async (status: string) => {
    if (!status) return;
    try {
      await post(`/applications/${app.id}/status`, { status, notes: "Updated from I Applied" });
      toast({ title: `${job?.company_name}: ${STATUS_LABELS[status as ApplicationStatus]}`, tone: "success" });
      onChanged();
    } catch (err) {
      toast({ title: "Could not update", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    }
  };

  return (
    <article className="group border bg-card p-5 transition-[border-color,transform] duration-200 hover:-translate-y-0.5 hover:border-foreground/60 motion-reduce:hover:translate-y-0">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <Link href={`/dashboard/applications/${app.id}`} className="font-semibold hover:text-primary">{job?.role_title}</Link>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1"><Building2 className="h-3 w-3" />{job?.company_name}</span>
            {job?.location && <span className="inline-flex items-center gap-1"><MapPin className="h-3 w-3" />{job.location}</span>}
            <span className="inline-flex items-center gap-1"><CalendarDays className="h-3 w-3" />
              applied {app.submitted_at ? formatDate(app.submitted_at) : "—"}{waited != null ? ` · ${waited === 0 ? "today" : `${waited} day${waited === 1 ? "" : "s"} ago`}` : ""}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <StatusBadge status={app.status} />
          <Select className="h-8 w-40 text-xs" aria-label={`Update status for ${job?.company_name}`} value="" onChange={(e) => updateStatus(e.target.value)}>
            <option value="">Update status…</option>
            {UPDATABLE.filter((s) => s !== app.status).map((s) => <option key={s} value={s}>{STATUS_LABELS[s]}</option>)}
          </Select>
        </div>
      </div>
      <ProgressRail status={app.status} />
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-xs">
        {nudge ? (
          <span className="inline-flex items-center gap-1.5 text-warning"><Clock3 className="h-3.5 w-3.5" />No reply after {waited} days — a short, polite follow-up often helps</span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-muted-foreground"><BellRing className="h-3.5 w-3.5" />Watching your inbox for replies</span>
        )}
        <div className="flex gap-3">
          {link && <a href={link} target="_blank" rel="noreferrer" className="label-caps inline-flex items-center gap-1 text-[11px] hover:text-primary">Posting <ExternalLink className="h-3 w-3" /></a>}
          <Link href={`/dashboard/applications/${app.id}`} className="label-caps inline-flex items-center gap-1 text-[11px] hover:text-primary">Details <ArrowUpRight className="h-3 w-3" /></Link>
        </div>
      </div>
    </article>
  );
}

// ------------------------------------------------------------------ log an application made anywhere
function LogDialog({ open, onOpenChange, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; onDone: () => void }) {
  const toast = useToast();
  const today = new Date().toISOString().slice(0, 10);
  const empty = { company_name: "", role_title: "", url: "", location: "", applied_on: today, job_type: "internship", notes: "" };
  const [form, setForm] = useState(empty);
  const [saving, setSaving] = useState(false);
  const set = (k: keyof typeof empty, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const submit = async () => {
    setSaving(true);
    try {
      const app = await post<ApplicationDetail>("/applications/manual", {
        ...form, url: form.url.trim() || null, location: form.location.trim() || null, notes: form.notes.trim() || null,
        applied_on: form.applied_on || null,
      });
      toast({ title: `Tracking ${app.job?.company_name}`, description: "Added to I Applied. You'll get updates on Gmail and here.", tone: "success" });
      setForm(empty);
      onOpenChange(false);
      onDone();
    } catch (err) {
      toast({ title: "Could not add it", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setSaving(false);
    }
  };
  const field = (k: keyof typeof empty, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <div className="space-y-1.5">
      <Label htmlFor={`log-${k}`}>{label}</Label>
      <Input id={`log-${k}`} value={form[k]} onChange={(e) => set(k, e.target.value)} {...props} />
    </div>
  );
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Log an application"
      description="Applied somewhere the agent didn't find it (a referral, a company site, Internshala…)? Add it and it's tracked like the rest."
      footer={<><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button onClick={submit} loading={saving} disabled={!form.company_name.trim() || !form.role_title.trim()}><CheckCheck /> Track it</Button></>}>
      <div className="grid gap-4 sm:grid-cols-2">
        {field("company_name", "Company", { placeholder: "Zomato" })}
        {field("role_title", "Role", { placeholder: "SDE Intern" })}
        <div className="sm:col-span-2">{field("url", "Link to the posting (optional)", { placeholder: "https://…", type: "url" })}</div>
        {field("location", "Location", { placeholder: "Gurugram, Haryana" })}
        {field("applied_on", "Applied on", { type: "date", max: today })}
        <div className="space-y-1.5">
          <Label htmlFor="log-job_type">Type</Label>
          <Select id="log-job_type" value={form.job_type} onChange={(e) => set("job_type", e.target.value)}>
            <option value="internship">Internship</option><option value="full-time">Full-time</option>
            <option value="part-time">Part-time</option><option value="contract">Contract</option>
          </Select>
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="log-notes">Notes (optional)</Label>
          <Textarea id="log-notes" rows={2} value={form.notes} onChange={(e) => set("notes", e.target.value)} placeholder="Referred by…, applied via…" />
        </div>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ page
export default function AppliedPage() {
  const toast = useToast();
  const refresh = useRefreshTracking();
  const { data: me } = useMe();
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [logOpen, setLogOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const statuses = FILTERS.find((f) => f.key === filter)?.statuses;
  const { data, isLoading, mutate } = useApplications({ applied_by: "me", status: statuses, q, page, page_size: 20, sort: "updated" });
  const counts = data?.counts || {};
  const sum = (list: ApplicationStatus[]) => list.reduce((n, s) => n + (counts[s] || 0), 0);
  const total = data?.self_applied_total ?? 0;
  const tiles = [total, sum(HEARD), sum(PROCESS), sum(OFFER)];
  const digest = me?.preferences.progress_digest ?? "daily";

  const sendReport = async () => {
    setSending(true);
    try {
      const res = await post<{ sent: boolean }>("/users/me/progress-report");
      toast(res.sent
        ? { title: "Progress report sent", description: "Check your Gmail (and Discord/Slack if connected).", tone: "success" }
        : { title: "Nothing to report yet", description: "Mark a job with “I Applied” first.", tone: "info" });
    } catch (err) {
      toast({ title: "Could not send the report", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setSending(false);
    }
  };

  const changed = () => { mutate(); refresh(); };

  return (
    <div>
      <PageHeader eyebrow="Tracked by you" title="I Applied"
        description={<>Every job you applied to on your own. The agent watches your inbox for replies and tells you about each update here, on Gmail and in Discord/Slack{digest !== "off" ? <>, plus a {digest} progress e-mail at about 8 PM</> : null}.</>}
        actions={
          <>
            <Button variant="outline" onClick={sendReport} loading={sending}><Mail /> Progress e-mail</Button>
            <Button onClick={() => setLogOpen(true)}><Plus /> Log an application</Button>
          </>
        } />

      <section aria-label="Your progress" className="mb-8 border">
        <div className="hidden border-b border-line/60 px-5 py-4 md:block"><TrackingLine /></div>
        <div className="grid grid-cols-2 lg:grid-cols-4 grid-lines">
          {STAGES.map((s, i) => <StageTile key={s.key} label={s.label} value={tiles[i]} total={total} index={i} />)}
        </div>
      </section>

      <div className="mb-4 flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button key={f.key} onClick={() => { setFilter(f.key); setPage(1); }} aria-pressed={filter === f.key}
            className={cn("rounded-full border px-3 py-1 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              filter === f.key ? "border-primary bg-primary text-primary-foreground" : "hover:bg-accent")}>
            {f.label}
            {f.statuses ? <span className="ml-1.5 opacity-80">{f.statuses.split(",").reduce((n, k) => n + (counts[k] || 0), 0)}</span> : <span className="ml-1.5 opacity-80">{total}</span>}
          </button>
        ))}
      </div>
      <div className="relative mb-4">
        <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input className="pl-9" placeholder="Search company or role…" aria-label="Search your applications" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      </div>

      <div className="space-y-3">
        {isLoading && Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[150px]" />)}
        {!isLoading && !data?.items.length && (
          <EmptyState icon={CheckCheck} title={total ? "Nothing matches" : "Nothing here yet"}
            description={total ? "Try another filter." : "Applied to a job yourself? Click “I Applied” on it in Swipe Review, Jobs or Applications — or log it here — and the agent tracks it from then on."}
            action={!total && (
              <div className="flex flex-wrap justify-center gap-2">
                <Button onClick={() => setLogOpen(true)}><Plus /> Log an application</Button>
                <Link href="/dashboard/jobs" className={buttonVariants({ variant: "outline" })}>Browse jobs</Link>
              </div>
            )} />
        )}
        {!!data?.items.length && (
          <Stagger key={`${filter}-${page}`} className="space-y-3">
            {data.items.map((app) => <StaggerItem key={app.id}><TrackedCard app={app} onChanged={changed} /></StaggerItem>)}
          </Stagger>
        )}
      </div>
      {data && data.total > 20 && (
        <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
          <span>{data.total} applications</span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
            <Button variant="outline" size="sm" disabled={page * 20 >= data.total} onClick={() => setPage((p) => p + 1)}>Next</Button>
          </div>
        </div>
      )}
      <LogDialog open={logOpen} onOpenChange={setLogOpen} onDone={changed} />
    </div>
  );
}
