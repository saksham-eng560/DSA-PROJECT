"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, ArrowRight, Check, CircleSlash, Clock, Layers, Loader2, Radar, Square, X } from "lucide-react";
import { AnimatedNumber, EASE_OUT } from "@/components/motion";
import { Button, buttonVariants } from "@/components/ui/button";
import type { useScan } from "@/hooks/use-scan";
import type { AgentRun, ScanPhase, ScanProgress, ScanSourceProgress } from "@/lib/types";
import { PLATFORM_LABELS, cn, titleCase } from "@/lib/utils";

type Scan = ReturnType<typeof useScan>;

const STEPS: { label: string; phases: ScanPhase[] }[] = [
  { label: "Search", phases: ["discovering"] },
  { label: "Save", phases: ["saving"] },
  { label: "Score", phases: ["scoring"] },
  { label: "Done", phases: ["finishing", "done"] },
];

function stepIndex(phase: ScanPhase | undefined) {
  const i = STEPS.findIndex((s) => phase && s.phases.includes(phase));
  return i < 0 ? 0 : i;
}

/** Re-render every second while `active`, for the elapsed clock. */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

function clock(seconds: number) {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return m ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

function etaText(eta: number | null | undefined) {
  if (!eta) return null;
  if (eta < 60) return `about ${Math.max(5, Math.round(eta / 5) * 5)}s left`;
  return `about ${Math.round(eta / 60)} min left`;
}

/** The bar: springs to each new value, with a sheen running across it while the scan works. */
export function ScanBar({ percent, active, className }: { percent: number; active: boolean; className?: string }) {
  const reduce = useReducedMotion();
  const value = Math.max(2, Math.min(100, percent));
  return (
    <div className={cn("relative h-2 w-full overflow-hidden bg-primary/15", className)} role="progressbar"
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)} aria-label="Scan progress">
      <motion.div className="absolute inset-y-0 left-0 overflow-hidden bg-primary"
        initial={false} animate={{ width: `${value}%` }}
        transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 90, damping: 22 }}>
        {active && !reduce && (
          <span aria-hidden className="absolute inset-y-0 left-0 w-1/4 animate-sheen bg-gradient-to-r from-transparent via-white/45 to-transparent" />
        )}
      </motion.div>
    </div>
  );
}

function SourceChip({ source }: { source: ScanSourceProgress }) {
  const label = PLATFORM_LABELS[source.name] || titleCase(source.name);
  const { status } = source;
  const icon = {
    pending: <Clock className="h-3.5 w-3.5 text-muted-foreground" />,
    running: <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />,
    done: <Check className="h-3.5 w-3.5 text-success" />,
    failed: <AlertTriangle className="h-3.5 w-3.5 text-warning" />,
    timeout: <CircleSlash className="h-3.5 w-3.5 text-muted-foreground" />,
  }[status];
  const detail = {
    pending: "Waiting",
    running: source.total ? `${source.done} / ${source.total}` : "Searching",
    done: `${source.found} found`,
    failed: "Couldn't load",
    timeout: "Too slow, skipped",
  }[status];
  return (
    <li title={source.error || undefined}
      className={cn("relative flex min-w-0 items-center gap-2 overflow-hidden border px-3 py-2 text-xs transition-colors",
        status === "running" ? "border-primary/60" : "border-line/70")}>
      {icon}
      <span className="min-w-0 flex-1 truncate font-semibold">{label}</span>
      <span className={cn("shrink-0 tabular-nums", status === "done" ? "text-foreground" : "text-muted-foreground")}>{detail}</span>
      {status === "running" && source.total > 0 && (
        <span aria-hidden className="absolute inset-x-0 bottom-0 h-0.5 bg-primary/15">
          <span className="block h-full bg-primary transition-[width] duration-500" style={{ width: `${(source.done / source.total) * 100}%` }} />
        </span>
      )}
    </li>
  );
}

function Counter({ label, value, of }: { label: string; value: number; of?: number }) {
  return (
    <div className="min-w-0">
      <p className="label-caps truncate text-[10px] text-muted-foreground">{label}</p>
      <p className="mt-1 font-display text-2xl leading-none">
        <AnimatedNumber value={value} duration={0.5} />
        {of != null && <span className="text-base text-muted-foreground"> / {of.toLocaleString()}</span>}
      </p>
    </div>
  );
}

function Running({ run, progress, scan }: { run: AgentRun; progress: ScanProgress | null; scan: Scan }) {
  const now = useNow(true);
  const elapsed = (now - new Date(run.started_at).getTime()) / 1000;
  const percent = progress?.percent ?? 1;
  const step = stepIndex(progress?.phase);
  const eta = etaText(progress?.eta_seconds);
  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="label-caps flex items-center gap-2 text-[11px] text-primary">
            <span className="h-2 w-2 animate-pulse-dot bg-primary" aria-hidden /> Scanning · {clock(elapsed)}{eta ? ` · ${eta}` : ""}
          </p>
          <p className="mt-3 flex items-baseline gap-4">
            <span className="font-display text-5xl leading-none tabular-nums sm:text-6xl">
              <AnimatedNumber value={percent} duration={0.6} className="tabular-nums" />%
            </span>
            <span className="min-w-0 text-sm text-muted-foreground" aria-live="polite">
              {progress?.message || (elapsed > 10
                ? "Waiting for the agent to pick up the scan (it's finishing other work first)…"
                : "Starting the scan…")}
              {progress?.phase === "scoring" && (progress.llm_total ?? 0) > 0 && (
                <span className="block text-xs">AI scored {progress.llm_done ?? 0} of {progress.llm_total}; the rest get an instant score if it&apos;s slow</span>
              )}
            </span>
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={scan.stop} loading={scan.stopping} disabled={run.status !== "running"}>
          {!scan.stopping && <Square className="fill-current" />} {scan.stopping ? "Stopping" : "Stop"}
        </Button>
      </div>

      <ScanBar percent={percent} active className="mt-5" />

      <ol className="mt-3 grid grid-cols-4 gap-2 text-[10px]" aria-label="Scan steps">
        {STEPS.map((s, i) => (
          <li key={s.label} className={cn("label-caps flex items-center gap-1.5 truncate",
            i < step ? "text-foreground" : i === step ? "text-primary" : "text-muted-foreground/70")}>
            <span className="font-mono">0{i + 1}</span> {s.label}
            {i < step && <Check className="h-3 w-3" aria-label="done" />}
          </li>
        ))}
      </ol>

      {!!progress?.sources.length && (
        <ul className="mt-6 grid gap-2 sm:grid-cols-2 xl:grid-cols-3" aria-label="Job sources">
          {progress.sources.map((s) => <SourceChip key={s.name} source={s} />)}
        </ul>
      )}

      <div className="mt-6 grid grid-cols-3 gap-4 border-t border-line/60 pt-5">
        <Counter label="Postings found" value={progress?.found ?? 0} />
        <Counter label="New for you" value={progress?.new ?? 0} />
        <Counter label="Scored" value={progress?.scored ?? 0} of={progress?.to_score || undefined} />
      </div>
    </>
  );
}

function Finished({ run, onDismiss }: { run: AgentRun; onDismiss: () => void }) {
  const ok = run.status === "completed";
  const stopped = run.status === "cancelled";
  const took = run.duration_seconds != null ? clock(run.duration_seconds) : null;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-4">
      <span className={cn("flex h-12 w-12 shrink-0 items-center justify-center",
        ok ? "bg-primary text-primary-foreground" : "border border-foreground/40")} aria-hidden>
        {ok ? <Check className="h-6 w-6" /> : stopped ? <Square className="h-5 w-5" /> : <AlertTriangle className="h-5 w-5 text-warning" />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="label-caps text-[11px] text-muted-foreground">
          {ok ? "Scan finished" : stopped ? "Scan stopped" : "Scan failed"}{took ? ` · ${took}` : ""}
        </p>
        <p className="mt-1 font-semibold">
          {ok ? run.progress?.message || `${run.jobs_discovered} new jobs`
            : stopped ? "Jobs already scored are waiting in Swipe Review." : run.progress?.message || "See the activity log for details."}
        </p>
        {!!run.errors_count && ok && (
          <p className="mt-1 text-xs text-muted-foreground">
            {run.errors_count} issue{run.errors_count === 1 ? "" : "s"} along the way — <Link href="/dashboard/logs" className="underline underline-offset-2 hover:text-primary">see the activity log</Link>.
          </p>
        )}
      </div>
      <div className="flex items-center gap-2">
        {ok || stopped ? (
          <Link href="/dashboard/review" className={buttonVariants({ size: "sm" })}><Layers /> Start swiping</Link>
        ) : (
          <Link href="/dashboard/logs" className={buttonVariants({ variant: "outline", size: "sm" })}>Activity log <ArrowRight /></Link>
        )}
        <Button variant="ghost" size="icon" onClick={onDismiss} aria-label="Dismiss"><X /></Button>
      </div>
    </div>
  );
}

/** The full progress panel for the Overview and Swipe Review pages. Hidden when no scan is running or just ended. */
export function ScanProgressPanel({ scan, className }: { scan: Scan; className?: string }) {
  const show = scan.running || scan.finished;
  return (
    <AnimatePresence initial={false}>
      {show && (
        <motion.section key="scan-panel" aria-label="Scan progress"
          initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.35, ease: EASE_OUT }} className={cn("overflow-hidden", className)}>
          <div className="mb-8 border border-foreground/70 bg-card p-5 sm:p-6">
            {scan.running ? <Running run={scan.running} progress={scan.progress} scan={scan} />
              : scan.finished && <Finished run={scan.finished} onDismiss={scan.dismissFinished} />}
          </div>
        </motion.section>
      )}
    </AnimatePresence>
  );
}

/** Compact indicator for the top bar on every page: percent plus a hairline bar along the header's bottom edge. */
export function ScanIndicator({ run }: { run: AgentRun | null }) {
  const reduce = useReducedMotion();
  const percent = run?.progress?.percent ?? 1;
  return (
    <AnimatePresence>
      {run && (
        <>
          <motion.span key="scan-line" aria-hidden className="absolute inset-x-0 bottom-0 block h-0.5 bg-primary/15"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <motion.span className="block h-full bg-primary" initial={false} animate={{ width: `${Math.max(2, percent)}%` }}
              transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 90, damping: 22 }} />
          </motion.span>
          <motion.span key="scan-pill" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}>
            <Link href="/dashboard" title={run.progress?.message || "Scanning"}
              className="label-caps mr-2 inline-flex items-center gap-2 border border-foreground/40 px-3 py-1.5 text-[11px] transition-colors hover:border-foreground">
              <Radar className="h-3.5 w-3.5 animate-spin text-primary [animation-duration:2.4s]" />
              <span className="hidden sm:inline">Scanning</span>
              <span className="tabular-nums">{Math.round(percent)}%</span>
            </Link>
          </motion.span>
        </>
      )}
    </AnimatePresence>
  );
}
