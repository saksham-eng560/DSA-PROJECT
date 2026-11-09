"use client";

import { forwardRef, useImperativeHandle, useState } from "react";
import { animate, motion, useMotionValue, useTransform, type PanInfo } from "framer-motion";
import { AlertTriangle, ArrowUpRight, Building2, CalendarDays, Globe2, Loader2, MapPin, Sparkles } from "lucide-react";
import { CompanyBadge, YearFitTag } from "@/components/company-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ReviewCard } from "@/lib/types";
import { PLATFORM_LABELS, cn, formatSalary, timeAgo, titleCase } from "@/lib/utils";

export type Decision = "keep" | "skip";
export type SwipeCardHandle = { fling: (decision: Decision) => Promise<void>; flyUp: () => Promise<void> };

const THRESHOLD = 120;
// [score key, label, short label for narrow cards]
const CRITERIA: [keyof ReviewCard["scores"], string, string][] = [
  ["skills_match", "Skills", "Skills"], ["experience_match", "Experience", "Exp."], ["industry_match", "Domain", "Domain"],
  ["location_match", "Location", "Place"], ["compensation_match", "Pay", "Pay"],
];

function sponsorshipTone(value: string | null): "success" | "danger" | "muted" {
  if (!value) return "muted";
  const v = value.toLowerCase();
  if (v.includes("offers")) return "success";
  if (v.includes("not") || v.includes("citizen")) return "danger";
  return "muted";
}

/** The draggable top card of the deck. Drag past the threshold (or fling) to decide. */
export const SwipeCard = forwardRef<SwipeCardHandle, {
  card: ReviewCard;
  onDecide: (decision: Decision) => void;
  onLoadDetails?: () => Promise<void>;
  disabled?: boolean;
}>(function SwipeCard({ card, onDecide, onLoadDetails, disabled }, ref) {
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const rotate = useTransform(x, [-320, 320], [-12, 12]);
  const appliedOpacity = useTransform(y, [-140, -20], [1, 0]);
  const keepOpacity = useTransform(x, [30, THRESHOLD], [0, 1]);
  const skipOpacity = useTransform(x, [-THRESHOLD, -30], [1, 0]);
  const [loadingDetails, setLoadingDetails] = useState(false);

  const fling = async (decision: Decision) => {
    const dir = decision === "keep" ? 1 : -1;
    await animate(x, dir * (typeof window !== "undefined" ? window.innerWidth : 900), { duration: 0.28, ease: [0.4, 0, 1, 1] });
    onDecide(decision);
  };
  // "I Applied": the card lifts off the deck instead of going left or right.
  const flyUp = async () => {
    await animate(y, -(typeof window !== "undefined" ? window.innerHeight : 900), { duration: 0.42, ease: [0.4, 0, 1, 1] });
  };
  useImperativeHandle(ref, () => ({ fling, flyUp }));

  const onDragEnd = (_: unknown, info: PanInfo) => {
    if (info.offset.x > THRESHOLD || info.velocity.x > 700) void fling("keep");
    else if (info.offset.x < -THRESHOLD || info.velocity.x < -700) void fling("skip");
    else animate(x, 0, { type: "spring", stiffness: 420, damping: 32 });
  };

  const job = card.job;
  const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
  const score = card.match_score;
  const shortDescription = (job.description || "").length < 400 && !!job.listing_source;

  return (
    <motion.article
      drag={disabled ? false : "x"}
      dragMomentum={false}
      onDragEnd={onDragEnd}
      style={{ x, y, rotate }}
      whileDrag={{ cursor: "grabbing" }}
      className="absolute inset-0 flex cursor-grab touch-pan-y select-none flex-col overflow-hidden border border-foreground/70 bg-background shadow-2xl"
      aria-label={`${job.role_title} at ${job.company_name}`}
    >
      {/* stamps */}
      <motion.span style={{ opacity: keepOpacity }}
        className="pointer-events-none absolute left-6 top-24 z-10 -rotate-12 border-4 border-primary bg-background/80 px-4 py-1 font-display text-3xl text-primary">
        KEEP
      </motion.span>
      <motion.span style={{ opacity: appliedOpacity }}
        className="pointer-events-none absolute left-1/2 top-24 z-10 -translate-x-1/2 border-4 border-success bg-background/80 px-4 py-1 font-display text-3xl text-success">
        APPLIED
      </motion.span>
      <motion.span style={{ opacity: skipOpacity }}
        className="pointer-events-none absolute right-6 top-24 z-10 rotate-12 border-4 border-foreground bg-background/80 px-4 py-1 font-display text-3xl text-foreground">
        SKIP
      </motion.span>

      <header className="flex items-start justify-between gap-4 border-b border-line/60 p-5 sm:p-6">
        <div className="min-w-0">
          <p className="label-caps text-[10px] text-muted-foreground">
            {PLATFORM_LABELS[job.source_platform] || titleCase(job.source_platform)}
            {job.listing_source ? " · curated list" : ""}
            {job.posted_date ? ` · posted ${timeAgo(job.posted_date)}` : ""}
          </p>
          <h2 className="display mt-3 text-2xl sm:text-[1.9rem]">{job.role_title}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-1.5 font-semibold text-foreground"><Building2 className="h-4 w-4" />{job.company_name}</span>
            {job.location && <span className="inline-flex min-w-0 items-center gap-1.5"><MapPin className="h-4 w-4 shrink-0" /><span className="truncate">{job.location}</span></span>}
            {job.is_remote && <span className="inline-flex items-center gap-1.5"><Globe2 className="h-4 w-4" />Remote</span>}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <CompanyBadge company={job.company_name} check={job.company} />
            <YearFitTag label={job.year_fit} />
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end">
          <span className={cn("font-display text-5xl leading-none tabular-nums", score != null && score >= 70 ? "text-primary" : "text-foreground")}>
            {score ?? "—"}
          </span>
          <span className="label-caps mt-1 text-[9px] text-muted-foreground">match</span>
        </div>
      </header>

      <div className="flex-1 space-y-5 overflow-y-auto p-5 scrollbar-thin sm:p-6">
        <div className="flex flex-wrap gap-1.5">
          <FocusBadges card={card} />
          {job.job_type && <Badge tone="outline">{titleCase(job.job_type)}</Badge>}
          {job.terms?.map((t) => <Badge key={t} tone="outline"><CalendarDays className="h-3 w-3" />{t}</Badge>)}
          {salary && <Badge tone="outline">{salary}</Badge>}
          {job.sponsorship && job.sponsorship !== "Other" && <Badge tone={sponsorshipTone(job.sponsorship)}>{job.sponsorship}</Badge>}
        </div>

        {card.heads_up.length > 0 && (
          <div className="flex items-start gap-2 border border-warning/50 bg-warning/10 p-3 text-sm">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <span>{card.heads_up.join(" · ")}</span>
          </div>
        )}

        {Object.keys(card.scores).length > 0 && (
          <div className="grid grid-cols-5 gap-2">
            {CRITERIA.map(([key, label, short]) => {
              const v = card.scores[key] ?? 0;
              return (
                <div key={key} title={`${label}: ${v}/20`}>
                  <div className="h-1 bg-foreground/10"><div className="h-full bg-primary" style={{ width: `${(v / 20) * 100}%` }} /></div>
                  <p className="mt-1.5 truncate text-[10px] uppercase tracking-wider text-muted-foreground">
                    <span className="sm:hidden" aria-hidden>{short}</span><span className="sr-only sm:not-sr-only">{label}</span>
                  </p>
                </div>
              );
            })}
          </div>
        )}

        {(card.strong_matches.length > 0 || card.missing_skills.length > 0) && (
          <div className="space-y-2">
            {card.strong_matches.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="label-caps mr-1 text-[10px] text-muted-foreground">You have</span>
                {card.strong_matches.slice(0, 8).map((s) => <Badge key={s} tone="primary">{s}</Badge>)}
              </div>
            )}
            {card.missing_skills.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="label-caps mr-1 text-[10px] text-muted-foreground">They want</span>
                {card.missing_skills.slice(0, 8).map((s) => <Badge key={s} tone="muted">{s}</Badge>)}
              </div>
            )}
          </div>
        )}

        {card.match_reasoning && <p className="text-sm leading-relaxed text-foreground/85">{card.match_reasoning}</p>}

        <div>
          <p className="label-caps mb-2 text-[10px] text-muted-foreground">About the role</p>
          <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">{job.description || "No description."}</p>
          {shortDescription && onLoadDetails && (
            <Button variant="outline" size="sm" className="mt-3" disabled={loadingDetails}
              onClick={async () => { setLoadingDetails(true); try { await onLoadDetails(); } finally { setLoadingDetails(false); } }}>
              {loadingDetails ? <Loader2 className="animate-spin" /> : null} Fetch full description
            </Button>
          )}
        </div>
      </div>

      <footer className="flex items-center justify-between border-t border-line/60 px-5 py-3 text-xs sm:px-6">
        <span className="text-muted-foreground">Found {timeAgo(card.discovered_at)}</span>
        <a href={job.application_url || job.source_url} target="_blank" rel="noreferrer"
          className="label-caps inline-flex items-center gap-1 text-[11px] font-bold hover:text-primary">
          Open posting <ArrowUpRight className="h-3.5 w-3.5" />
        </a>
      </footer>
    </motion.article>
  );
});

/** Where the job sits in your location focus (e.g. Delhi NCR first) and whether it's the season you want. */
function FocusBadges({ card }: { card: ReviewCard }) {
  const f = card.focus;
  if (!f) return null;
  return (
    <>
      {f.location_tier === 0 && <Badge tone="primary"><MapPin className="h-3 w-3" />Prime location</Badge>}
      {f.location_tier === 1 && f.country && <Badge tone="outline"><MapPin className="h-3 w-3" />{f.country}</Badge>}
      {f.location_tier === 3 && f.country && <Badge tone="muted">Outside {f.country}</Badge>}
      {f.season === "match" && f.season_label && <Badge tone="success"><Sparkles className="h-3 w-3" />{f.season_label}</Badge>}
      {f.season === "immediate" && <Badge tone="muted">Starts immediately</Badge>}
    </>
  );
}

/** A static card peeking out under the top card. */
export function DeckShadowCard({ card, depth }: { card: ReviewCard; depth: number }) {
  return (
    <div aria-hidden
      className="absolute inset-0 flex flex-col border border-line bg-card p-6 transition-transform duration-300"
      style={{ transform: `translateY(${depth * 24}px) scale(${1 - depth * 0.035})`, zIndex: -depth, opacity: 1 - depth * 0.3 }}>
      <p className="label-caps text-[10px] text-muted-foreground">{card.job.company_name}</p>
      <p className="display mt-3 line-clamp-2 text-xl text-foreground/60">{card.job.role_title}</p>
    </div>
  );
}
