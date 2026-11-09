"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  AlertTriangle, ArrowLeft, Building2, CheckCircle2, Download, ExternalLink, FileText, ListChecks, MapPin, RefreshCw, Save,
  ShieldCheck, Sparkles, XCircle,
} from "lucide-react";
import { ApprovalModal } from "@/components/approval-modal";
import { IAppliedButton, SelfAppliedTag, canSelfApply } from "@/components/i-applied-button";
import { JobMatchBadge } from "@/components/job-match-badge";
import { StatusBadge } from "@/components/status-badge";
import { StatusTimeline } from "@/components/status-timeline";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { useApplication } from "@/hooks/use-applications";
import { ApiError, patch, post } from "@/lib/api-client";
import type { ApplicationStatus, CustomAnswer } from "@/lib/types";
import { PLATFORM_LABELS, STATUS_LABELS, cn, formatDate, formatDateTime, formatSalary, titleCase } from "@/lib/utils";

const MANUAL_STATUSES: ApplicationStatus[] = [
  "applied", "acknowledged", "screening", "interview", "assessment", "final_round", "offer", "accepted", "rejected", "withdrawn",
];
const SUBSCORES = [
  ["skills_match", "Skills"], ["experience_match", "Experience"], ["industry_match", "Industry"],
  ["location_match", "Location"], ["compensation_match", "Compensation"],
] as const;

export default function ApplicationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: app, mutate, isLoading } = useApplication(id);
  const toast = useToast();
  const [coverLetter, setCoverLetter] = useState("");
  const [answers, setAnswers] = useState<CustomAnswer[]>([]);
  const [dirty, setDirty] = useState(false);
  const [approveOpen, setApproveOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (app && !dirty) {
      setCoverLetter(app.cover_letter || "");
      setAnswers(app.custom_answers || []);
    }
  }, [app, dirty]);

  if (isLoading || !app) {
    return <div className="space-y-4"><Skeleton className="h-24" /><Skeleton className="h-96" /></div>;
  }
  const job = app.job;
  const reviewable = ["pending_approval", "failed", "matched"].includes(app.status);
  const details = (app.match_details || {}) as Record<string, unknown>;
  const failure = app.status === "failed" ? [...app.history].reverse().find((h) => h.new_status === "failed")?.notes : null;

  const run = async (label: string, fn: () => Promise<unknown>, success: string) => {
    setBusy(label);
    try {
      await fn();
      toast({ title: success, tone: "success" });
      await mutate();
    } catch (err) {
      toast({ title: "Action failed", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setBusy(null);
    }
  };

  const saveEdits = () => run("save", async () => {
    await patch(`/applications/${app.id}`, { cover_letter: coverLetter, custom_answers: answers });
    setDirty(false);
  }, "Changes saved");

  const approve = async () => {
    await run("approve", async () => {
      await post(`/applications/${app.id}/approve`, { cover_letter: coverLetter, custom_answers: answers });
      setDirty(false);
    }, "Approved — the agent is submitting your application");
  };

  const updateAnswer = (index: number, value: string) => {
    setAnswers((prev) => prev.map((a, i) => (i === index ? { ...a, answer: value, needs_user_review: false } : a)));
    setDirty(true);
  };

  return (
    <div>
      <Link href="/dashboard/applications" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Applications
      </Link>

      <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-4">
          <JobMatchBadge score={app.match_score} size="lg" />
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="display text-2xl sm:text-[1.9rem]">{job?.role_title}</h1>
              <StatusBadge status={app.status} />
              {app.self_applied && <SelfAppliedTag />}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
              <span className="inline-flex items-center gap-1"><Building2 className="h-4 w-4" />{job?.company_name}</span>
              {job?.location && <span className="inline-flex items-center gap-1"><MapPin className="h-4 w-4" />{job.location}{job.is_remote && " · Remote"}</span>}
              {job && formatSalary(job.salary_min, job.salary_max, job.salary_currency) && <span>{formatSalary(job.salary_min, job.salary_max, job.salary_currency)}</span>}
              <span>via {PLATFORM_LABELS[app.ats_platform || ""] || titleCase(app.ats_platform)}</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {job?.source_url && <a href={job.source_url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", size: "sm" })}>Job posting <ExternalLink /></a>}
              {job?.application_url && job.application_url !== job.source_url && (
                <a href={job.application_url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", size: "sm" })}>Application form <ExternalLink /></a>
              )}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {canSelfApply(app.status) && <IAppliedButton applicationId={app.id} size="default" onApplied={() => mutate()} />}
          {reviewable && (
            <>
              {dirty && <Button variant="outline" onClick={saveEdits} loading={busy === "save"}><Save /> Save edits</Button>}
              <Button variant="outline" onClick={() => run("skip", () => post(`/applications/${app.id}/skip`), "Skipped")} loading={busy === "skip"}>
                <XCircle /> Skip
              </Button>
              {app.status === "pending_approval" && (
                <Link href={`/dashboard/submit?id=${app.id}`} className={buttonVariants()}><ListChecks /> Review & submit</Link>
              )}
              <Button variant={app.status === "pending_approval" ? "outline" : "default"} onClick={() => setApproveOpen(true)}>
                <ShieldCheck /> Review & approve
              </Button>
            </>
          )}
          {!reviewable && !["approved", "preparing"].includes(app.status) && (
            <Select className="w-44" aria-label="Update status" value="" onChange={(e) => e.target.value && run("status", () => post(`/applications/${app.id}/status`, { status: e.target.value }), "Status updated")}>
              <option value="">Update status…</option>
              {MANUAL_STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABELS[s]}</option>)}
            </Select>
          )}
        </div>
      </div>

      {app.status === "preparing" && (
        <div className="mb-4 flex items-center gap-3 border border-primary/30 bg-primary/5 p-3 text-sm">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          The agent is tailoring your resume, writing the cover letter and filling out the form… This page updates by itself when it&apos;s done.
        </div>
      )}
      {app.status === "failed" && (
        <div className="mb-4 flex flex-col gap-3 border border-primary/50 bg-primary/10 p-3 text-sm sm:flex-row sm:items-start">
          <XCircle className="mt-0.5 hidden h-4 w-4 shrink-0 text-primary sm:block" />
          <div className="min-w-0 flex-1">
            <p className="font-medium">This application didn&apos;t go through. Nothing was sent.</p>
            <p className="break-words text-muted-foreground">{failure || app.error_log || "Something went wrong."}</p>
            <p className="mt-1 text-muted-foreground">Try again, or apply on the site yourself and click “I Applied”.</p>
          </div>
          <Button size="sm" className="shrink-0" loading={busy === "retry"}
            onClick={() => run("retry", () => post(`/applications/${app.id}/prepare`), "Trying again: it waits for you in Ready to submit when it's done")}>
            {busy !== "retry" && <RefreshCw />} Try again
          </Button>
        </div>
      )}
      {app.status === "approved" && (
        <div className="mb-4 flex items-center gap-3 border border-primary/30 bg-primary/5 p-3 text-sm">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          Approved — submitting now. {app.notes}
        </div>
      )}
      {app.needs_manual_review && app.manual_review_reason && reviewable && (
        <div className="mb-4 flex items-start gap-3 border border-warning/50 bg-warning/10 p-3 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          <div className="flex-1">
            <p className="font-medium">Needs your attention</p>
            <p className="text-muted-foreground">{app.manual_review_reason}</p>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button size="sm" variant="outline" onClick={() => run("restage", () => post(`/applications/${app.id}/restage`), "Re-filling the form")} loading={busy === "restage"}>
              <RefreshCw /> Re-fill form
            </Button>
            <IAppliedButton applicationId={app.id} onApplied={() => mutate()} />
          </div>
        </div>
      )}
      {app.status === "applied" && app.confirmation_number && (
        <div className="mb-4 flex items-center gap-2 border border-success/40 bg-success/10 p-3 text-sm">
          <CheckCircle2 className="h-4 w-4 text-success" /> Submitted {formatDateTime(app.submitted_at)} · confirmation #{app.confirmation_number}
        </div>
      )}

      <Tabs defaultValue="review">
        <TabsList className="h-auto w-full flex-wrap">
          <TabsTrigger value="review">Filled form</TabsTrigger>
          <TabsTrigger value="resume">Tailored resume</TabsTrigger>
          <TabsTrigger value="cover">Cover letter</TabsTrigger>
          <TabsTrigger value="answers">
            Answers {answers.some((a) => a.needs_user_review) && <span className="h-1.5 w-1.5 rounded-full bg-warning" />}
          </TabsTrigger>
          <TabsTrigger value="match">Match analysis</TabsTrigger>
          <TabsTrigger value="job">Job description</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>

        <TabsContent value="review">
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-5">
            <Card className="xl:col-span-3">
              <CardHeader>
                <CardTitle>Form screenshot</CardTitle>
                <CardDescription>{app.staged_at ? `Filled ${formatDateTime(app.staged_at)} — not submitted` : "The form hasn't been filled yet"}</CardDescription>
              </CardHeader>
              <CardContent>
                {app.form_screenshot_url ? (
                  <a href={app.form_screenshot_url} target="_blank" rel="noreferrer">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={app.form_screenshot_url} alt="Filled application form" className="max-h-[640px] w-full border object-contain object-top" />
                  </a>
                ) : <p className="text-sm text-muted-foreground">No screenshot available.</p>}
                {app.confirmation_screenshot_url && (
                  <div className="mt-4">
                    <p className="mb-2 text-sm font-medium">Confirmation page</p>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={app.confirmation_screenshot_url} alt="Submission confirmation" className="max-h-80 w-full border object-contain object-top" />
                  </div>
                )}
              </CardContent>
            </Card>
            <Card className="xl:col-span-2">
              <CardHeader>
                <CardTitle>Fields</CardTitle>
                <CardDescription>
                  {app.form_fields.filter((f) => f.status === "filled").length} filled ·{" "}
                  {app.form_fields.filter((f) => f.status === "unmapped").length} need input
                </CardDescription>
              </CardHeader>
              <CardContent className="max-h-[640px] space-y-1 overflow-y-auto scrollbar-thin">
                {!app.form_fields.length && <p className="text-sm text-muted-foreground">No field report yet.</p>}
                {app.form_fields.map((f, i) => (
                  <div key={`${f.label}-${i}`} className="flex items-start gap-2 px-2 py-1.5 text-sm hover:bg-accent">
                    {f.status === "filled" ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" /> :
                      f.status === "unmapped" ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" /> :
                        <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-muted-foreground/40" />}
                    <div className="min-w-0">
                      <p className="truncate font-medium">{f.label}{f.required && <span className="text-destructive"> *</span>}</p>
                      <p className="truncate text-xs text-muted-foreground">{f.value || (f.status === "unmapped" ? "Needs your input" : "Left blank (optional)")}</p>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="resume">
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <div>
                  <CardTitle>Tailored resume</CardTitle>
                  <CardDescription>The exact PDF that will be uploaded</CardDescription>
                </div>
                {app.tailored_resume_pdf_url && (
                  <a href={app.tailored_resume_pdf_url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", size: "sm" })}><Download /> PDF</a>
                )}
              </CardHeader>
              <CardContent>
                {app.tailored_resume_pdf_url ? (
                  <iframe src={app.tailored_resume_pdf_url} title="Tailored resume" className="h-[720px] w-full border" />
                ) : <p className="text-sm text-muted-foreground">Not generated yet.</p>}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-primary" /> What changed</CardTitle>
                <CardDescription>Reordered and rephrased — never invented</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                {app.tailored_resume?.changes_made.filter((c) => !c.startsWith("[guard]")).map((c) => (
                  <p key={c} className="flex gap-2"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />{c}</p>
                ))}
                {!!app.tailored_resume?.changes_made.some((c) => c.startsWith("[guard]")) && (
                  <div className="mt-4 border border-primary/20 bg-primary/5 p-3">
                    <p className="mb-1 flex items-center gap-1.5 font-medium"><ShieldCheck className="h-4 w-4 text-primary" /> Truthfulness guard</p>
                    <p className="mb-2 text-xs text-muted-foreground">Claims that couldn&apos;t be verified against your master resume were removed:</p>
                    {app.tailored_resume.changes_made.filter((c) => c.startsWith("[guard]")).map((c) => (
                      <p key={c} className="text-xs text-muted-foreground">• {c.replace("[guard] ", "")}</p>
                    ))}
                  </div>
                )}
                {!app.tailored_resume && (
                  <p className="text-muted-foreground">
                    {app.tailored_resume_pdf_url ? "Your original resume file is sent unchanged (Settings › Mass apply › Resume to send)." : "No tailored resume yet."}
                  </p>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="cover">
          <Card>
            <CardHeader>
              <CardTitle>Cover letter</CardTitle>
              <CardDescription>Edit freely — your version is what gets submitted. {coverLetter.trim().split(/\s+/).filter(Boolean).length} words.</CardDescription>
            </CardHeader>
            <CardContent>
              <Textarea value={coverLetter} onChange={(e) => { setCoverLetter(e.target.value); setDirty(true); }}
                className="min-h-[420px] font-serif text-[15px] leading-relaxed" disabled={!reviewable} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="answers">
          <Card>
            <CardHeader>
              <CardTitle>Application questions</CardTitle>
              <CardDescription>Answers the agent will enter. Low-confidence answers are flagged — please check them.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {!answers.length && <p className="text-sm text-muted-foreground">This form had no custom questions.</p>}
              {answers.map((a, i) => (
                <div key={`${a.question}-${i}`} className={cn("border p-3", a.needs_user_review && "border-warning/60 bg-warning/5")}>
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <p className="text-sm font-medium">{a.question}{a.required && <span className="text-destructive"> *</span>}</p>
                    <div className="flex shrink-0 gap-1">
                      {a.needs_user_review && <Badge tone="warning">Review</Badge>}
                      {a.confidence != null && <Badge tone="muted">{Math.round(a.confidence * 100)}%</Badge>}
                      {a.source && <Badge tone="outline">{a.source}</Badge>}
                    </div>
                  </div>
                  {a.options && a.options.length ? (
                    <Select aria-label={a.question} value={a.answer} onChange={(e) => updateAnswer(i, e.target.value)} disabled={!reviewable}>
                      {!a.options.includes(a.answer) && <option value={a.answer}>{a.answer || "Select…"}</option>}
                      {a.options.map((o) => <option key={o} value={o}>{o}</option>)}
                    </Select>
                  ) : (
                    <Textarea aria-label={a.question} value={a.answer} onChange={(e) => updateAnswer(i, e.target.value)} disabled={!reviewable}
                      className={cn(a.answer.length < 80 ? "min-h-[40px]" : "min-h-[110px]")} />
                  )}
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="match">
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Score breakdown</CardTitle>
                <CardDescription>{String(details.method || "") === "llm" ? "Evaluated by Claude" : "Heuristic evaluation"} · each criterion scored 0–20</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {SUBSCORES.map(([key, label]) => {
                  const value = Number(details[key] ?? 0);
                  return (
                    <div key={key}>
                      <div className="mb-1 flex justify-between text-sm"><span>{label}</span><span className="font-medium tabular-nums">{value}/20</span></div>
                      <Progress value={(value / 20) * 100} />
                    </div>
                  );
                })}
                {app.match_reasoning && <p className="pt-2 text-sm text-muted-foreground">{app.match_reasoning}</p>}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Skills</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <p className="mb-2 text-sm font-medium">Strong matches</p>
                  <div className="flex flex-wrap gap-1.5">
                    {((details.strong_matches as string[]) || []).map((s) => <Badge key={s} tone="success">{s}</Badge>)}
                    {!((details.strong_matches as string[]) || []).length && <span className="text-sm text-muted-foreground">—</span>}
                  </div>
                </div>
                <div>
                  <p className="mb-2 text-sm font-medium">Missing / gaps</p>
                  <div className="flex flex-wrap gap-1.5">
                    {((details.missing_skills as string[]) || []).map((s) => <Badge key={s} tone="warning">{s}</Badge>)}
                    {!((details.missing_skills as string[]) || []).length && <span className="text-sm text-muted-foreground">None detected</span>}
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="job">
          <Card>
            <CardHeader>
              <CardTitle>{job?.role_title}</CardTitle>
              <CardDescription>
                {titleCase(job?.job_type)} · {titleCase(job?.experience_level)} · posted {formatDate(job?.posted_date)}
                {job?.deadline_date && ` · apply by ${formatDate(job.deadline_date)}`}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="prose-pre max-h-[640px] overflow-y-auto text-muted-foreground">{job?.description}</div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="activity">
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle>Timeline</CardTitle></CardHeader>
              <CardContent><StatusTimeline history={app.history} /></CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Emails & interviews</CardTitle></CardHeader>
              <CardContent className="space-y-3 text-sm">
                {!app.communications.length && !app.interviews.length && <p className="text-muted-foreground">No recruiter contact yet.</p>}
                {app.interviews.map((i) => (
                  <Link key={i.id} href={`/dashboard/interviews?id=${i.id}`} className="block border p-3 hover:bg-accent">
                    <p className="font-medium">{titleCase(i.interview_type || "interview")} · {formatDateTime(i.scheduled_at)}</p>
                    <p className="text-xs text-muted-foreground">{i.meeting_link || i.physical_location}</p>
                  </Link>
                ))}
                {app.communications.map((c) => (
                  <Link key={c.id} href={`/dashboard/emails?id=${c.id}`} className="block border p-3 hover:bg-accent">
                    <div className="flex items-center justify-between gap-2">
                      <p className="truncate font-medium">{c.subject}</p>
                      {c.detected_intent && <Badge tone="info">{titleCase(c.detected_intent)}</Badge>}
                    </div>
                    <p className="text-xs text-muted-foreground">{c.sender_name || c.sender_email} · {formatDateTime(c.received_at)}</p>
                  </Link>
                ))}
                {app.error_log && (
                  <div className="bg-destructive/10 p-3 text-xs text-destructive">
                    <p className="mb-1 flex items-center gap-1 font-medium"><FileText className="h-3.5 w-3.5" /> Last error</p>
                    {app.error_log}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      <ApprovalModal app={app} open={approveOpen} onOpenChange={setApproveOpen} onApprove={approve} answers={answers} coverLetter={coverLetter} />
    </div>
  );
}
