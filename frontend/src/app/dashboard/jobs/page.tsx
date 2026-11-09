"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { Briefcase, Building2, ExternalLink, Link2, MapPin, Plus, RefreshCw, Search, Wand2 } from "lucide-react";
import { CompanyBadge, CompanyTag } from "@/components/company-badge";
import { EmptyState } from "@/components/empty-state";
import { IAppliedButton, canSelfApply } from "@/components/i-applied-button";
import { Stagger, StaggerItem } from "@/components/motion";
import { JobMatchBadge } from "@/components/job-match-badge";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Modal } from "@/components/modal";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { ApiError, fetcher, post } from "@/lib/api-client";
import type { ApplicationSummary, Job, Paginated } from "@/lib/types";
import { PLATFORM_LABELS, formatDate, formatSalary, timeAgo, titleCase } from "@/lib/utils";

const PREPARABLE = ["discovered", "matched", "skipped", "failed"];

function JobDialog({ jobId, onClose, onChanged }: { jobId: string | null; onClose: () => void; onChanged: () => void }) {
  const { data: job, mutate } = useSWR<Job>(jobId ? `/jobs/${jobId}` : null, fetcher);
  const toast = useToast();
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const act = async (kind: "prepare" | "evaluate") => {
    if (!job) return;
    setBusy(kind);
    try {
      await post(`/jobs/${job.id}/${kind}`);
      const appId = job.application?.id;
      toast({ title: kind === "prepare" ? "Preparing application" : "Match re-evaluated", tone: "success",
        description: kind === "prepare" ? "Tailoring your resume and filling the form. It waits for you in Ready to submit when it's done." : undefined,
        action: kind === "prepare" && appId ? { label: "Track it", onClick: () => router.push(`/dashboard/applications/${appId}`) } : undefined });
      mutate();
      onChanged();
    } catch (err) {
      toast({ title: "Failed", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setBusy(null);
    }
  };
  return (
    <Modal open={!!jobId} onOpenChange={(o) => !o && onClose()} className="max-w-3xl"
      title={job ? job.role_title : "Loading…"}
      description={job ? `${job.company_name}${job.location ? ` · ${job.location}` : ""}` : undefined}
      footer={job && (
        <>
          {(job.source_url || job.application_url) && (
            <a href={job.source_url || job.application_url || undefined} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline" })}>
              Posting <ExternalLink />
            </a>
          )}
          <Button variant="outline" onClick={() => act("evaluate")} loading={busy === "evaluate"}><RefreshCw /> Re-evaluate</Button>
          {job.application && canSelfApply(job.application.status) && (
            <IAppliedButton applicationId={job.application.id} size="default" onApplied={() => { mutate(); onChanged(); }} />
          )}
          {job.application && PREPARABLE.includes(job.application.status) ? (
            <Button onClick={() => act("prepare")} loading={busy === "prepare"}><Wand2 /> Prepare application</Button>
          ) : job.application && (
            <Link href={`/dashboard/applications/${job.application.id}`} className={buttonVariants()}>Open application</Link>
          )}
        </>
      )}>
      {!job ? <Skeleton className="h-64" /> : (
        <div className="space-y-4">
          <div className="flex items-start gap-4 bg-muted/50 p-3">
            <JobMatchBadge score={job.application?.match_score} />
            <div className="text-sm">
              <CompanyBadge company={job.company_name} check={job.company} className="mb-2" />
              {job.application && <StatusBadge status={job.application.status} />}
              <p className="mt-1 text-muted-foreground">{job.application?.match_reasoning || "Not evaluated yet."}</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {job.extracted_skills.slice(0, 20).map((s) => <Badge key={s} tone="muted">{s}</Badge>)}
          </div>
          <div className="prose-pre text-muted-foreground">{job.description}</div>
        </div>
      )}
    </Modal>
  );
}

function ImportDialog({ open, onOpenChange, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; onDone: () => void }) {
  const [url, setUrl] = useState("");
  const [prepare, setPrepare] = useState(true);
  const [loading, setLoading] = useState(false);
  const toast = useToast();
  const submit = async () => {
    setLoading(true);
    try {
      const app = await post<ApplicationSummary>("/jobs/import", { url, prepare });
      toast({ title: `Imported: ${app.job?.role_title}`, description: `Match score ${app.match_score ?? "—"}`, tone: "success" });
      setUrl("");
      onOpenChange(false);
      onDone();
    } catch (err) {
      toast({ title: "Import failed", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setLoading(false);
    }
  };
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Add a job by URL"
      description="Paste any Greenhouse, Lever, Ashby, Workday, LinkedIn or company careers-page link."
      footer={<><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button onClick={submit} loading={loading} disabled={!url.startsWith("http")}>Import job</Button></>}>
      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="job-url">Job posting URL</Label>
          <Input id="job-url" placeholder="https://job-boards.greenhouse.io/company/jobs/123" value={url} onChange={(e) => setUrl(e.target.value)} />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={prepare} onChange={(e) => setPrepare(e.target.checked)} className="h-4 w-4" />
          Tailor resume, write cover letter and fill the form right away (you still approve before submission)
        </label>
      </div>
    </Modal>
  );
}

export default function JobsPage() {
  const [q, setQ] = useState("");
  const [platform, setPlatform] = useState("");
  const [remote, setRemote] = useState("");
  const [minScore, setMinScore] = useState("");
  const [sort, setSort] = useState("match");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const params = new URLSearchParams({ sort, page: String(page), page_size: "25" });
  if (q) params.set("q", q);
  if (platform) params.set("platform", platform);
  if (remote) params.set("remote", remote);
  if (minScore) params.set("min_score", minScore);
  const { data, isLoading, mutate } = useSWR<Paginated<Job>>(`/jobs?${params}`, fetcher, { keepPreviousData: true });

  return (
    <div>
      <PageHeader title="Discovered jobs" description="Everything the agent found, scored against your master resume and preferences."
        actions={<Button onClick={() => setImportOpen(true)}><Plus /> Add job by URL</Button>} />
      <div className="mb-4 grid gap-2 md:grid-cols-[1fr_repeat(4,10rem)]">
        <div className="relative">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input className="pl-9" placeholder="Search title, company, location…" aria-label="Search jobs" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        </div>
        <Select aria-label="Source" value={platform} onChange={(e) => { setPlatform(e.target.value); setPage(1); }}>
          <option value="">All sources</option>
          {["linkedin", "indeed", "glassdoor", "wellfound", "greenhouse", "lever", "ashby", "workday", "custom"].map((p) => (
            <option key={p} value={p}>{PLATFORM_LABELS[p]}</option>
          ))}
        </Select>
        <Select aria-label="Location" value={remote} onChange={(e) => { setRemote(e.target.value); setPage(1); }}>
          <option value="">Any location</option>
          <option value="true">Remote only</option>
          <option value="false">On-site / hybrid</option>
        </Select>
        <Select aria-label="Minimum match score" value={minScore} onChange={(e) => { setMinScore(e.target.value); setPage(1); }}>
          <option value="">Any score</option>
          <option value="80">80+</option>
          <option value="60">60+</option>
          <option value="40">40+</option>
        </Select>
        <Select aria-label="Sort jobs" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="match">Best match</option>
          <option value="recent">Recently found</option>
          <option value="posted">Recently posted</option>
          <option value="company">Company A–Z</option>
        </Select>
      </div>

      <div className="overflow-hidden border bg-card">
        {isLoading && <div className="space-y-2 p-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-14" />)}</div>}
        {!isLoading && !data?.items.length && (
          <div className="p-6"><EmptyState icon={Briefcase} title="No jobs yet" description="Configure your job sources in Settings, then run a scan from the Overview page — or add a job by URL."
            action={<Button variant="outline" onClick={() => setImportOpen(true)}><Link2 /> Add job by URL</Button>} /></div>
        )}
        <Stagger as="ul" key={`${sort}-${page}-${platform}-${remote}-${minScore}`} className={data && isLoading ? "opacity-60" : undefined}>
          {data?.items.map((job) => (
            <StaggerItem as="li" key={job.id} className="flex items-center border-b transition-colors last:border-0 hover:bg-accent/50">
              <button onClick={() => setSelected(job.id)} className="flex min-w-0 flex-1 items-center gap-4 px-4 py-3 text-left">
                <JobMatchBadge score={job.application?.match_score} size="sm" />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{job.role_title}</p>
                  <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1"><Building2 className="h-3 w-3" />{job.company_name}</span>
                    <CompanyTag check={job.company} />
                    {job.location && <span className="inline-flex items-center gap-1"><MapPin className="h-3 w-3" />{job.location}</span>}
                    {formatSalary(job.salary_min, job.salary_max, job.salary_currency) && <span>{formatSalary(job.salary_min, job.salary_max, job.salary_currency)}</span>}
                    <span>{titleCase(job.job_type)}</span>
                  </div>
                </div>
                <div className="hidden shrink-0 flex-col items-end gap-1 text-xs text-muted-foreground sm:flex">
                  <span>{PLATFORM_LABELS[job.source_platform] || job.source_platform}{job.easy_apply ? " · Easy Apply" : ""}{job.is_remote ? " · Remote" : ""}</span>
                  <span>{job.posted_date ? `posted ${formatDate(job.posted_date)}` : `found ${timeAgo(job.discovered_at)}`}</span>
                </div>
                {job.application && <StatusBadge status={job.application.status} />}
              </button>
              {job.application && canSelfApply(job.application.status) && (
                <IAppliedButton applicationId={job.application.id} className="mr-4 hidden md:inline-flex" onApplied={() => mutate()} />
              )}
            </StaggerItem>
          ))}
        </Stagger>
      </div>
      {data && data.total > 25 && (
        <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
          <span>{data.total} jobs</span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
            <Button variant="outline" size="sm" disabled={page * 25 >= data.total} onClick={() => setPage((p) => p + 1)}>Next</Button>
          </div>
        </div>
      )}
      <JobDialog jobId={selected} onClose={() => setSelected(null)} onChanged={() => mutate()} />
      <ImportDialog open={importOpen} onOpenChange={setImportOpen} onDone={() => mutate()} />
    </div>
  );
}
