"use client";

import { useState } from "react";
import { useSWRConfig } from "swr";
import { GraduationCap, ShieldAlert, ShieldCheck, ShieldQuestion, ThumbsDown, ThumbsUp, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useToast } from "@/components/ui/toast";
import { ApiError, post } from "@/lib/api-client";
import type { CompanyCheck, CompanyVerdict } from "@/lib/types";
import { cn } from "@/lib/utils";

const LOOK: Record<CompanyVerdict, { icon: typeof ShieldCheck; label: string; tone: string; means: string }> = {
  verified: {
    icon: ShieldCheck, label: "Verified", tone: "border-success/60 text-success",
    means: "The company check passed: the agent may apply here by itself.",
  },
  unverified: {
    icon: ShieldQuestion, label: "Unverified", tone: "border-foreground/25 text-muted-foreground",
    means: "Nothing wrong found, nothing proven either: the agent never applies here by itself. Check it, then submit it yourself or mark it legit.",
  },
  suspicious: {
    icon: ShieldAlert, label: "Possible fraud", tone: "border-primary/70 text-primary",
    means: "This posting shows scam signs, so it's skipped and nothing is sent to it.",
  },
};

/** The company-check verdict, with its reasons and your "Mark legit" / "Not legit" override. */
export function CompanyBadge({ company, check, className, showTier = true }: {
  company: string; check?: CompanyCheck | null; className?: string; showTier?: boolean;
}) {
  const verdict: CompanyVerdict = check?.verdict ?? "unverified";
  const look = LOOK[verdict];
  const Icon = look.icon;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const { mutate } = useSWRConfig();
  const toast = useToast();

  const decide = async (trusted: boolean | null) => {
    setBusy(String(trusted));
    try {
      const res = await post<{ applications_updated: number }>("/jobs/company-trust", { company, trusted });
      const n = res.applications_updated;
      toast({
        title: trusted === true ? `${company} marked legit` : trusted === false ? `${company} marked not legit` : `Back to the agent's check for ${company}`,
        description: trusted === true
          ? `The agent applies to it automatically from now on${n ? `; ${n} waiting application${n === 1 ? " is" : "s are"} going out` : ""}.`
          : trusted === false ? `It's avoided from now on${n ? `; ${n} waiting job${n === 1 ? "" : "s"} skipped` : ""}.` : undefined,
        tone: trusted === false ? "info" : "success",
      });
      setOpen(false);
      await mutate(() => true); // every list and card shows the new verdict
    } catch (err) {
      toast({ title: "Couldn't save that", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <span className={cn("inline-flex flex-wrap items-center gap-1.5", className)}>
      {showTier && check?.tier_label && (
        <span className="label-caps border border-foreground/60 px-1.5 py-0.5 text-[10px] text-foreground">{check.tier_label}</span>
      )}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button type="button" onPointerDown={(e) => e.stopPropagation()} /* never starts a swipe */
            className={cn("label-caps inline-flex items-center gap-1 border px-1.5 py-0.5 text-[10px] transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", look.tone)}
            aria-label={`Company check for ${company}: ${look.label}. Show why`}>
            <Icon className="h-3 w-3" aria-hidden /> {look.label}
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[min(22rem,calc(100vw-2rem))] text-sm" onPointerDown={(e) => e.stopPropagation()}>
          <p className={cn("flex items-center gap-1.5 font-semibold", look.tone.split(" ").find((c) => c.startsWith("text-")))}>
            <Icon className="h-4 w-4" aria-hidden /> {company}: {look.label}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">{look.means}</p>
          {!!check?.reasons?.length && (
            <ul className="mt-3 space-y-1 text-xs">
              {check.reasons.map((r) => (
                <li key={r} className={cn("flex gap-1.5", r.startsWith("⚠") ? "text-primary" : "text-foreground")}>
                  <span aria-hidden>{r.startsWith("⚠") ? "" : "·"}</span>{r}
                </li>
              ))}
            </ul>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            {check?.method === "you" ? (
              <Button size="sm" variant="outline" loading={busy === "null"} onClick={() => void decide(null)}><Undo2 /> Undo “legit”</Button>
            ) : verdict !== "verified" ? (
              <Button size="sm" variant="outline" loading={busy === "true"} onClick={() => void decide(true)}><ThumbsUp /> Mark legit</Button>
            ) : null}
            <Button size="sm" variant="ghost" loading={busy === "false"} onClick={() => void decide(false)}><ThumbsDown /> Not legit</Button>
          </div>
        </PopoverContent>
      </Popover>
    </span>
  );
}

/** The same verdict as a plain tag, for places that are already a button (list rows). */
export function CompanyTag({ check, className }: { check?: CompanyCheck | null; className?: string }) {
  const look = LOOK[check?.verdict ?? "unverified"];
  const Icon = look.icon;
  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      {check?.tier_label && <span className="label-caps border border-foreground/60 px-1 text-[9px] text-foreground">{check.tier_label}</span>}
      <span className={cn("label-caps inline-flex items-center gap-1 border px-1 text-[9px]", look.tone)} title={check?.reasons?.join("\n")}>
        <Icon className="h-2.5 w-2.5" aria-hidden /> {look.label}
      </span>
    </span>
  );
}

/** "Open to 2nd-year students": the posting names your year of study (Google STEP, "1st and 2nd year students"). */
export function YearFitTag({ label, className }: { label?: string | null; className?: string }) {
  if (!label) return null;
  return (
    <span className={cn("label-caps inline-flex items-center gap-1 border border-success/60 px-1.5 py-0.5 text-[10px] text-success", className)}>
      <GraduationCap className="h-3 w-3" aria-hidden /> {label}
    </span>
  );
}
