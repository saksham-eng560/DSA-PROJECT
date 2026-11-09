import { cn } from "@/lib/utils";

export function scoreTone(score: number | null | undefined) {
  if (score == null) return "border-border text-muted-foreground";
  if (score >= 80) return "border-primary bg-primary text-primary-foreground";
  if (score >= 60) return "border-foreground text-foreground";
  if (score >= 40) return "border-foreground/50 text-foreground/80";
  return "border-border text-muted-foreground";
}

/** Square score block; red when it's a strong match. */
export function JobMatchBadge({ score, size = "md" }: { score: number | null | undefined; size?: "sm" | "md" | "lg" }) {
  const dims = { sm: "h-8 w-8 text-xs", md: "h-11 w-11 text-sm", lg: "h-16 w-16 text-2xl" }[size];
  return (
    <div className={cn("flex shrink-0 items-center justify-center border font-display tabular-nums", dims, scoreTone(score))}
      title={score == null ? "Not evaluated yet" : `Match score ${score}/100`} aria-label={score == null ? "Not scored" : `Match score ${score}`}>
      {score ?? "—"}
    </div>
  );
}
