"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useSWRConfig } from "swr";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { CheckCheck } from "lucide-react";
import { Button, type ButtonProps } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { ApiError, post } from "@/lib/api-client";
import type { ApplicationDetail, ApplicationStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Statuses before "applied": the only ones where "I Applied" makes sense. */
const BEFORE_APPLIED: ApplicationStatus[] = ["discovered", "matched", "skipped", "preparing", "pending_approval", "approved", "failed", "withdrawn"];

export function canSelfApply(status: ApplicationStatus | undefined | null) {
  return !!status && BEFORE_APPLIED.includes(status);
}

/** Refresh every list that shows applications, jobs or the deck. */
export function useRefreshTracking() {
  const { mutate } = useSWRConfig();
  return () => mutate((key) => typeof key === "string" && ["/applications", "/jobs", "/review", "/agent", "/analytics", "/notifications"].some((p) => key.startsWith(p)));
}

const BURST = [0, 60, 120, 180, 240, 300];

/**
 * "I Applied": tell the agent you applied on your own. The job moves to Applied (and the
 * I Applied section), the agent stops working on it and tracks replies from then on.
 */
export function IAppliedButton({ applicationId, onApplied, size = "sm", variant = "outline", className, label = "I Applied" }: {
  applicationId: string;
  onApplied?: (app: ApplicationDetail) => void;
  size?: ButtonProps["size"];
  variant?: ButtonProps["variant"];
  className?: string;
  label?: string;
}) {
  const toast = useToast();
  const refresh = useRefreshTracking();
  const reduce = useReducedMotion();
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  // A different application (e.g. the next card in Ready to submit): start fresh, never "Applied" from the last one.
  useEffect(() => setState("idle"), [applicationId]);

  const click = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (state !== "idle") return;
    setState("busy");
    try {
      const app = await post<ApplicationDetail>(`/applications/${applicationId}/mark-applied`);
      setState("done");
      toast({
        title: `Tracking ${app.job?.company_name ?? "this application"}`,
        description: "Moved to Applied. You'll get updates on Gmail and here whenever they reply.",
        tone: "success",
      });
      window.setTimeout(() => {
        onApplied?.(app);
        refresh();
      }, reduce ? 0 : 650);
    } catch (err) {
      setState("idle");
      toast({ title: "Could not mark as applied", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    }
  };

  return (
    <Button type="button" size={size} variant={state === "done" ? "success" : variant} onClick={click} loading={state === "busy"}
      aria-live="polite" className={cn("relative overflow-visible", className)} title="I applied to this job on my own — track it">
      <span className="relative inline-flex items-center">
        {state === "done" ? (
          <svg viewBox="0 0 24 24" className="size-4" aria-hidden>
            <motion.path d="M4 12.5l5 5L20 6.5" fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="square"
              initial={reduce ? false : { pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.35, ease: "easeOut" }} />
          </svg>
        ) : state === "idle" && <CheckCheck />}
        <AnimatePresence>
          {state === "done" && !reduce && BURST.map((deg) => (
            <motion.span key={deg} aria-hidden
              className="absolute left-1/2 top-1/2 h-[2px] w-2 origin-left bg-success"
              style={{ rotate: deg }}
              initial={{ x: 0, opacity: 1, scaleX: 0.4 }}
              animate={{ x: [0, 14], opacity: [1, 0], scaleX: [0.4, 1] }}
              transition={{ duration: 0.5, ease: "easeOut" }} />
          ))}
        </AnimatePresence>
      </span>
      {state === "done" ? "Applied" : label}
    </Button>
  );
}

/** Small "applied by you" marker for cards and headers. */
export function SelfAppliedTag({ className }: { className?: string }) {
  return (
    <Link href="/dashboard/applied" onClick={(e) => e.stopPropagation()}
      className={cn("label-caps inline-flex shrink-0 items-center gap-1 border border-success/60 px-2 py-0.5 text-[10px] text-success hover:bg-success hover:text-success-foreground", className)}>
      <CheckCheck className="h-3 w-3" /> You applied
    </Link>
  );
}
