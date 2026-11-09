"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { ArrowUpRight, Building2, Check, Crown, MapPin, Radar, Search } from "lucide-react";
import { CompanyBadge, YearFitTag } from "@/components/company-badge";
import { EmptyState } from "@/components/empty-state";
import { JobMatchBadge } from "@/components/job-match-badge";
import { PageHeader } from "@/components/page-header";
import { ScanProgressPanel } from "@/components/scan-progress";
import { StatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { useScan } from "@/hooks/use-scan";
import { ApiError, fetcher, post } from "@/lib/api-client";
import type { CompanyTier, Job, TopCompanies } from "@/lib/types";
import { cn, timeAgo } from "@/lib/utils";

const KEEPABLE = ["discovered", "matched"];

function JobRow({ job, onChanged }: { job: Job; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const app = job.application;
  const keep = async () => {
    if (!app) return;
    setBusy(true);
    try {
      await post(`/review/${app.id}`, { decision: "keep" });
      toast({
        title: `Kept: ${job.company_name}`,
        description: job.company?.verdict === "verified"
          ? "The agent prepares and applies to it (verified company)."
          : "The agent prepares it; it waits in Ready to submit for your OK.",
        tone: "success",
      });
      onChanged();
    } catch (err) {
      toast({ title: "Couldn't keep it", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="flex flex-col gap-3 border-b border-line/60 px-4 py-4 last:border-0 sm:flex-row sm:items-center sm:gap-5 sm:px-5">
      <JobMatchBadge score={app?.match_score} size="sm" />
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-x-2 text-sm">
          <span className="inline-flex items-center gap-1.5 font-semibold"><Building2 className="h-4 w-4" />{job.company_name}</span>
          <CompanyBadge company={job.company_name} check={job.company} />
          <YearFitTag label={job.year_fit} />
        </p>
        <p className="mt-1 break-words font-display text-lg leading-tight">{job.role_title}</p>
        <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
          {job.location && <span className="inline-flex items-center gap-1"><MapPin className="h-3 w-3" />{job.location}</span>}
          {job.is_remote && !/remote/i.test(job.location || "") && <span>Remote</span>}
          <span>found {timeAgo(job.discovered_at)}</span>
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
        {app && <StatusBadge status={app.status} />}
        {app && KEEPABLE.includes(app.status) && (
          <Button size="sm" onClick={() => void keep()} loading={busy}>{!busy && <Check />} Keep</Button>
        )}
        {app && !KEEPABLE.includes(app.status) && app.status !== "skipped" && (
          <Link href={`/dashboard/applications/${app.id}`} className={buttonVariants({ variant: "outline", size: "sm" })}>Open</Link>
        )}
        {job.source_url && (
          <a href={job.source_url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "ghost", size: "sm" })}
            aria-label={`Open the ${job.company_name} posting`}>Posting <ArrowUpRight /></a>
        )}
      </div>
    </li>
  );
}

export default function TopCompaniesPage() {
  const scan = useScan();
  const [tier, setTier] = useState<CompanyTier | "all">("all");
  const [q, setQ] = useState("");
  const params = new URLSearchParams();
  if (tier !== "all") params.set("tier", tier);
  if (q.trim()) params.set("q", q.trim());
  const { data, isLoading, mutate } = useSWR<TopCompanies>(`/jobs/top-companies${params.size ? `?${params}` : ""}`, fetcher);
  const [showCatalog, setShowCatalog] = useState(false);
  const scanning = !!scan.running;
  const tracked = data ? Object.values(data.catalog).reduce((n, list) => n + list.length, 0) : 0;

  return (
    <div>
      <PageHeader eyebrow="Mass apply" title="Top companies"
        description="Internships at big tech, product-based companies, renowned Indian and global startups and AI companies. Every company is checked: only verified ones are applied to automatically."
        actions={
          <Button onClick={() => void scan.start(["top_companies"])} loading={scan.starting} disabled={scanning}>
            {!scan.starting && <Radar />} {scanning ? "Scanning…" : "Scan top companies"}
          </Button>
        } />

      <ScanProgressPanel scan={scan} />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div role="tablist" aria-label="Company category" className="flex flex-wrap gap-2">
          {[{ key: "all" as const, label: "All", count: data?.total ?? 0 }, ...(data?.tiers ?? [])].map((t) => (
            <button key={t.key} type="button" role="tab" aria-selected={tier === t.key} onClick={() => setTier(t.key)}
              className={cn("label-caps inline-flex items-center gap-2 border px-3 py-1.5 text-[11px] transition-colors",
                tier === t.key ? "border-primary bg-primary text-primary-foreground" : "border-foreground/30 hover:border-foreground")}>
              {t.label} <span className="tabular-nums opacity-80">{t.count}</span>
            </button>
          ))}
        </div>
        <div className="relative sm:w-64">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Company, role or city" className="pl-9" aria-label="Search top companies" />
        </div>
      </div>

      <div className="mt-6 border border-foreground/70 bg-background">
        {isLoading ? (
          <div className="space-y-2 p-4"><Skeleton className="h-16" /><Skeleton className="h-16" /><Skeleton className="h-16" /></div>
        ) : data && data.items.length ? (
          <ul aria-label="Internships at top companies">
            {data.items.map((job) => <JobRow key={job.id} job={job} onChanged={() => void mutate()} />)}
          </ul>
        ) : (
          <EmptyState icon={Crown} title={q || tier !== "all" ? "Nothing here yet" : "No top-company internships yet"}
            description={q || tier !== "all" ? "Try another category or search."
              : `Scan ${tracked || "100+"} renowned companies' own job boards (and LinkedIn for the ones with their own career sites).`}
            action={!q && tier === "all" ? (
              <Button onClick={() => void scan.start(["top_companies"])} loading={scan.starting} disabled={scanning}><Radar /> Scan top companies</Button>
            ) : undefined} />
        )}
      </div>

      {data && (
        <section className="mt-8" aria-label="Companies the agent tracks">
          <button type="button" onClick={() => setShowCatalog(!showCatalog)} aria-expanded={showCatalog}
            className="label-caps text-[11px] text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
            {showCatalog ? "Hide" : "Show"} the {tracked} companies the agent tracks
          </button>
          {showCatalog && (
            <div className="mt-4 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {data.tiers.map((t) => (
                <div key={t.key}>
                  <p className="label-caps text-[10px] text-muted-foreground">{t.label}</p>
                  <p className="mt-2 text-sm leading-relaxed">{data.catalog[t.key].join(" · ")}</p>
                </div>
              ))}
            </div>
          )}
          {!data.scan_top_companies && (
            <p className="mt-3 text-xs text-muted-foreground">
              Your regular scans skip these companies: turn on “Search top companies in every scan” in{" "}
              <Link href="/dashboard/settings" className="underline underline-offset-2 hover:text-primary">Settings</Link>.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
