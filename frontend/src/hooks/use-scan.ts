"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { useToast } from "@/components/ui/toast";
import { useAgentStatus } from "@/hooks/use-applications";
import { ApiError, fetcher, post } from "@/lib/api-client";
import type { AgentRun, AgentStatus, ScanProgress } from "@/lib/types";

/** Apply a `scan_progress` WebSocket event to the cached agent status, without a refetch. */
export function applyScanProgress(status: AgentStatus | undefined, runId: string, progress: ScanProgress) {
  if (!status) return status;
  let hit = false;
  const running_runs = status.running_runs.map((r) => {
    if (r.id !== runId) return r;
    hit = true;
    return { ...r, progress };
  });
  return hit ? { ...status, running_runs } : status;
}

/**
 * The scan that's running right now (or just finished), with start / stop.
 * `finished` holds the last scan for a while after it ends, so the bar can say how it went.
 */
export function useScan() {
  const toast = useToast();
  const { mutate } = useSWRConfig();
  const { data: status, mutate: refreshStatus } = useAgentStatus();
  const running = status?.running_runs.find((r) => r.run_type === "scan") ?? null;
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [finishedId, setFinishedId] = useState<string | null>(null);
  const lastRunning = useRef<string | null>(null);

  useEffect(() => {
    if (running) {
      lastRunning.current = running.id;
      setFinishedId(null);
    } else if (lastRunning.current) {
      setFinishedId(lastRunning.current); // it just ended: fetch the final result once
      lastRunning.current = null;
      setStopping(false);
      mutate((key) => typeof key === "string" && ["/review", "/applications", "/jobs", "/analytics"].some((p) => key.startsWith(p)));
    }
  }, [running, mutate]);

  const { data: finished } = useSWR<AgentRun>(finishedId ? `/agent/runs/${finishedId}` : null, fetcher);

  /** Start a scan: your saved sources (plus the top companies), or only `platforms` when given. */
  const start = useCallback(async (platforms?: unknown) => {
    setStarting(true);
    try {
      await post("/agent/start-scan", Array.isArray(platforms) ? { platforms } : {}); // (a click event is not a list)
      toast({ title: "Scan started", description: "Watch the progress bar; jobs land in Swipe Review as they're scored.", tone: "success" });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast({ title: "A scan is already running", description: "Its progress is shown below.", tone: "info" });
      } else {
        toast({ title: "Could not start scan", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
      }
    } finally {
      await refreshStatus();
      setStarting(false);
    }
  }, [refreshStatus, toast]);

  const stop = useCallback(async () => {
    if (!running) return;
    setStopping(true);
    try {
      await post(`/agent/runs/${running.id}/cancel`);
      toast({ title: "Stopping the scan", description: "Jobs already scored stay in Swipe Review.", tone: "info" });
      await refreshStatus();
    } catch (err) {
      setStopping(false);
      toast({ title: "Could not stop the scan", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    }
  }, [running, refreshStatus, toast]);

  return {
    status,
    running,
    progress: running?.progress ?? null,
    finished: !running && finished && finished.id === finishedId ? finished : null,
    dismissFinished: () => setFinishedId(null),
    start,
    stop,
    starting,
    stopping,
    busy: starting || !!running,
  };
}
