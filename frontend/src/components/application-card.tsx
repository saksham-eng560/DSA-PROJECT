import Link from "next/link";
import { AlertTriangle, ArrowUpRight, Building2, MapPin } from "lucide-react";
import { IAppliedButton, SelfAppliedTag, canSelfApply } from "@/components/i-applied-button";
import { JobMatchBadge } from "@/components/job-match-badge";
import { StatusBadge } from "@/components/status-badge";
import type { ApplicationSummary } from "@/lib/types";
import { PLATFORM_LABELS, timeAgo } from "@/lib/utils";

export function ApplicationCard({ app, showIApplied = true, action }: {
  app: ApplicationSummary;
  showIApplied?: boolean;
  /** An extra button next to the status (e.g. "Review & submit"). */
  action?: React.ReactNode;
}) {
  const job = app.job;
  return (
    <div className="group relative flex items-center gap-4 border bg-card p-4 transition-[border-color,transform] duration-200 hover:-translate-y-0.5 hover:border-foreground/60 motion-reduce:hover:translate-y-0">
      <Link href={`/dashboard/applications/${app.id}`} className="flex min-w-0 flex-1 items-center gap-4 outline-none after:absolute after:inset-0 focus-visible:after:ring-2 focus-visible:after:ring-ring">
        <JobMatchBadge score={app.match_score} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate font-semibold">{job?.role_title}</p>
            {app.needs_manual_review && <AlertTriangle className="h-4 w-4 shrink-0 text-warning" aria-label="Needs review" />}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1"><Building2 className="h-3 w-3" />{job?.company_name}</span>
            {job?.location && <span className="inline-flex items-center gap-1"><MapPin className="h-3 w-3" />{job.location}{job.is_remote && !/remote/i.test(job.location) ? " · Remote" : ""}</span>}
            <span>{PLATFORM_LABELS[job?.source_platform || ""] || job?.source_platform}</span>
            <span>updated {timeAgo(app.updated_at)}</span>
          </div>
        </div>
      </Link>
      {/* Above the stretched link so they stay clickable */}
      <div className="relative z-10 flex shrink-0 items-center gap-2">
        {app.self_applied && <SelfAppliedTag className="hidden md:inline-flex" />}
        {showIApplied && canSelfApply(app.status) && <IAppliedButton applicationId={app.id} className="hidden sm:inline-flex" />}
        {action}
        <StatusBadge status={app.status} />
      </div>
      <ArrowUpRight className="hidden h-4 w-4 text-muted-foreground transition-colors group-hover:text-primary sm:block" />
    </div>
  );
}
