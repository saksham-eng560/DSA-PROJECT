"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Crosshair } from "lucide-react";
import type { Preferences } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATIONS = ["Scan", "Swipe", "Tailor & fill", "Apply", "Track"];
const CHIPS = [
  { role: "SDE Intern", place: "Gurugram" },
  { role: "ML Intern", place: "Noida" },
  { role: "Data Intern", place: "New Delhi" },
];
const LOOP = 7.5; // seconds for a chip to cross the whole pipeline

/**
 * Landing-page motion graphic: internship cards glide through the agent's pipeline; each station
 * lights up as a card passes it. Static (cards resting at stations) when "reduce motion" is on.
 */
export function PipelineMotion({ className }: { className?: string }) {
  const reduce = useReducedMotion();
  const gap = LOOP / CHIPS.length;
  return (
    <div className={cn("relative", className)} aria-label="Scan, swipe, tailor and fill, apply, track: the agent's pipeline" role="img">
      <div className="grid grid-cols-5 text-center">
        {STATIONS.map((name, i) => <p key={name} className="label-caps text-[10px] text-muted-foreground">0{i + 1}</p>)}
      </div>
      {/* rail through the stations; a red pulse runs along it and each station lights up in turn */}
      <div className="relative mt-4 grid h-6 grid-cols-5 items-center">
        <div className="absolute inset-x-[10%] top-1/2 h-px bg-line" aria-hidden />
        {!reduce && (
          <motion.div aria-hidden className="absolute top-1/2 h-[3px] w-28 -translate-y-1/2 bg-gradient-to-r from-transparent to-primary"
            initial={{ left: "4%" }} animate={{ left: ["4%", "82%"] }}
            transition={{ duration: LOOP / 2, repeat: Infinity, ease: "easeInOut", repeatDelay: 0.4 }} />
        )}
        {STATIONS.map((name, i) => (
          <span key={name} className="relative mx-auto flex h-6 w-6 items-center justify-center border border-foreground/70 bg-background" aria-hidden>
            {!reduce && (
              <motion.span className="absolute inset-0 bg-primary" initial={{ opacity: 0 }}
                animate={{ opacity: [0, 1, 0] }}
                transition={{ duration: 0.9, repeat: Infinity, repeatDelay: gap - 0.9, delay: (i / (STATIONS.length - 1)) * LOOP * 0.86 }} />
            )}
            {reduce && i === STATIONS.length - 1 && <span className="absolute inset-0 bg-primary" />}
          </span>
        ))}
      </div>
      <div className="mt-4 grid grid-cols-5 text-center">
        {STATIONS.map((name) => <p key={name} className="label-caps text-[10px] font-bold sm:text-[11px]">{name}</p>)}
      </div>
      {/* travelling internship cards */}
      <div className="relative mt-8 h-16 overflow-hidden" aria-hidden>
        {CHIPS.map((chip, i) => (
          <motion.div key={chip.role}
            className="absolute top-2 flex w-40 flex-col border border-foreground/60 bg-background px-3 py-2 shadow-lg"
            style={reduce ? { left: `${6 + i * 32}%` } : undefined}
            initial={reduce ? false : { left: "-22%", opacity: 0 }}
            animate={reduce ? undefined : { left: ["-22%", "4%", "84%", "104%"], opacity: [0, 1, 1, 0] }}
            transition={reduce ? undefined : { duration: LOOP, times: [0, 0.1, 0.9, 1], repeat: Infinity, ease: "linear", delay: i * gap }}>
            <span className="truncate text-xs font-semibold">{chip.role}</span>
            <span className="truncate text-[11px] text-muted-foreground">{chip.place} · Summer 2027</span>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

/** Overview strip: what the agent is hunting for right now, with a live "radar" dot. */
export function FocusStrip({ prefs }: { prefs: Preferences }) {
  const reduce = useReducedMotion();
  const focus = prefs.location_focus;
  const on = focus && focus.enabled !== false && focus.country;
  const first = focus?.prime_cities?.[0];
  const prime = first && /delhi/i.test(first) && focus?.prime_cities.some((c) => /ncr/i.test(c)) ? "Delhi NCR" : first;
  const parts = [
    prefs.job_types.length === 1 && prefs.job_types[0] === "internship" ? "Internships only" : prefs.job_types.join(" + "),
    prefs.internship_season || null,
    on ? `~${focus.country_share}% ${focus.country}` : null,
    on && prime ? `${prime} first` : null,
  ].filter(Boolean);
  return (
    <div className="mb-8 flex flex-wrap items-center gap-x-4 gap-y-2 border px-5 py-3 text-sm">
      <span className="relative flex h-5 w-5 items-center justify-center text-primary" aria-hidden>
        <Crosshair className="h-4 w-4" />
        {!reduce && (
          <motion.span className="absolute inset-0 rounded-full border border-primary"
            initial={{ scale: 0.6, opacity: 0.9 }} animate={{ scale: 1.8, opacity: 0 }}
            transition={{ duration: 1.8, repeat: Infinity, ease: "easeOut" }} />
        )}
      </span>
      <span className="label-caps text-[11px] text-muted-foreground">Hunting for</span>
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1 font-semibold">
        {parts.map((p, i) => (
          <span key={String(p)} className="flex items-center gap-3">{i > 0 && <span className="h-1 w-1 bg-primary" aria-hidden />}{p}</span>
        ))}
      </span>
      <Link href="/dashboard/settings?tab=preferences" className="label-caps ml-auto inline-flex items-center gap-1 text-[11px] hover:text-primary">
        Change <ArrowRight className="h-3 w-3" />
      </Link>
    </div>
  );
}
