"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import useSWR from "swr";
import { ScrollText } from "lucide-react";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { fetcher } from "@/lib/api-client";
import type { AgentRun, Paginated } from "@/lib/types";
import { cn, formatDateTime, timeAgo, titleCase } from "@/lib/utils";

function RunDetail({ id }: { id: string }) {
  const { data: run } = useSWR<AgentRun>(`/agent/runs/${id}`, fetcher, { refreshInterval: (d) => (d?.status === "running" ? 3000 : 0) });
  if (!run) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{titleCase(run.run_type)} run</CardTitle>
        <CardDescription>
          {formatDateTime(run.started_at)} · {run.duration_seconds != null ? `${run.duration_seconds}s` : "running"} · triggered by {run.trigger}
        </CardDescription>
        <div className="flex flex-wrap gap-2 pt-1 text-xs">
          <Badge tone="muted">{run.jobs_discovered} discovered</Badge>
          <Badge tone="muted">{run.jobs_matched} matched</Badge>
          <Badge tone="muted">{run.applications_prepared} prepared</Badge>
          <Badge tone="muted">{run.applications_submitted} submitted</Badge>
          {!!run.errors_count && <Badge tone="danger">{run.errors_count} errors</Badge>}
        </div>
      </CardHeader>
      <CardContent>
        <div className="max-h-[560px] overflow-y-auto bg-muted/40 p-3 font-mono text-xs">
          {(run.log || []).map((entry, i) => (
            <div key={i} className="flex gap-3 py-0.5">
              <span className="shrink-0 text-muted-foreground">{new Date(entry.ts).toLocaleTimeString()}</span>
              <span className={cn("shrink-0 uppercase", entry.level === "error" ? "text-destructive" : entry.level === "warning" ? "text-warning" : "text-muted-foreground")}>{entry.level}</span>
              <span className="whitespace-pre-wrap break-words">{entry.message}</span>
            </div>
          ))}
          {!run.log?.length && <p className="text-muted-foreground">No log entries.</p>}
        </div>
      </CardContent>
    </Card>
  );
}

function LogsInner() {
  const params = useSearchParams();
  const [selected, setSelected] = useState<string | null>(params.get("run"));
  const [type, setType] = useState("");
  const { data } = useSWR<Paginated<AgentRun>>(`/agent/runs?page_size=50${type ? `&run_type=${type}` : ""}`, fetcher, { refreshInterval: 10000 });
  return (
    <div>
      <PageHeader title="Agent logs" description="An audit trail of every scan, preparation and submission the agent performed." />
      <Select className="mb-4 w-48" aria-label="Run type" value={type} onChange={(e) => setType(e.target.value)}>
        <option value="">All runs</option>
        <option value="scan">Scans</option>
        <option value="prepare">Preparations</option>
        <option value="apply">Submissions</option>
      </Select>
      {!data?.items.length ? <EmptyState icon={ScrollText} title="No agent runs yet" description="Start a scan from the Overview page." /> : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <div className="space-y-2">
            {data.items.map((r) => (
              <button key={r.id} onClick={() => setSelected(r.id)}
                className={cn("flex w-full items-center justify-between border bg-card p-3 text-left text-sm hover:bg-accent/50", selected === r.id && "border-primary ring-1 ring-primary")}>
                <span className="flex items-center gap-2">
                  <span className={cn("h-2 w-2 rounded-full", r.status === "completed" ? "bg-success" : r.status === "failed" ? "bg-destructive" : "bg-warning")} />
                  <span className="font-medium">{titleCase(r.run_type)}</span>
                  <span className="text-xs text-muted-foreground">{r.status}</span>
                </span>
                <span className="text-xs text-muted-foreground">{timeAgo(r.started_at)}</span>
              </button>
            ))}
          </div>
          <div>{selected ? <RunDetail id={selected} /> : <EmptyState icon={ScrollText} title="Select a run to see its log" />}</div>
        </div>
      )}
    </div>
  );
}

export default function LogsPage() {
  return <Suspense><LogsInner /></Suspense>;
}
