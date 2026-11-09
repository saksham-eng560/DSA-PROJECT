import Link from "next/link";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ logo */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 20" className={cn("h-5 w-4", className)} aria-hidden>
      <path d="M2 1h7v4H6v10h3v4H2z" fill="currentColor" />
      <path d="M11 7h3v6h-3z" fill="hsl(var(--primary))" />
    </svg>
  );
}

export function Logo({ href = "/", className }: { href?: string; className?: string }) {
  return (
    <Link href={href} className={cn("inline-flex items-center gap-2 text-[19px] leading-none tracking-tight", className)}>
      <LogoMark />
      <span><span className="font-bold">Auto</span>Apply</span>
    </Link>
  );
}

/* ------------------------------------------------------------------ tunnel grid
 * A wire-frame tunnel seen head-on: rounded-square rings receding in perspective, joined by
 * rails, with a dot at every joint. Generated once at module load (pure SVG, server-rendered).
 */
const RINGS = 17;
const RAILS = 44;
const W = 600;
const H = 900;

function squircle(t: number, a: number, b: number, n = 3.4): [number, number] {
  const c = Math.cos(t);
  const s = Math.sin(t);
  const x = a * Math.sign(c) * Math.abs(c) ** (2 / n);
  const y = b * Math.sign(s) * Math.abs(s) ** (2 / n);
  return [W / 2 + x, H / 2 + y];
}

const scales = Array.from({ length: RINGS }, (_, i) => 1 / (1 + 0.21 * i));
const angles = Array.from({ length: RAILS }, (_, j) => (j / RAILS) * Math.PI * 2 + Math.PI / RAILS);
const round = (v: number) => Math.round(v * 10) / 10;

const RING_PATHS = scales.map((s) => {
  const pts = Array.from({ length: 96 }, (_, k) => squircle((k / 96) * Math.PI * 2, 420 * s, 610 * s));
  return `M${pts.map(([x, y]) => `${round(x)},${round(y)}`).join("L")}Z`;
});
const RAIL_PATHS = angles.map((t) =>
  `M${scales.map((s) => squircle(t, 420 * s, 610 * s)).map(([x, y]) => `${round(x)},${round(y)}`).join("L")}`);
const DOTS = scales.flatMap((s, i) => angles.map((t) => {
  const [x, y] = squircle(t, 420 * s, 610 * s);
  return { x: round(x), y: round(y), r: round(Math.max(0.8, 3.2 * s)), o: round(Math.min(1, 0.35 + s)), k: `${i}-${t}` };
}));

export function TunnelGrid({ className, animated = true }: { className?: string; animated?: boolean }) {
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice" className={cn("h-full w-full text-foreground", className)} aria-hidden>
      <defs>
        <radialGradient id="tunnel-fade" cx="50%" cy="50%" r="55%">
          <stop offset="0%" stopColor="hsl(var(--background))" stopOpacity="1" />
          <stop offset="35%" stopColor="hsl(var(--background))" stopOpacity="0.55" />
          <stop offset="100%" stopColor="hsl(var(--background))" stopOpacity="0" />
        </radialGradient>
      </defs>
      <g className={cn(animated && "origin-center animate-tunnel-drift motion-reduce:animate-none")} style={{ transformBox: "fill-box" }}>
        <g fill="none" stroke="currentColor" strokeWidth="0.9" strokeOpacity="0.55">
          {RING_PATHS.map((d) => <path key={d.slice(0, 24)} d={d} />)}
          {RAIL_PATHS.map((d) => <path key={d.slice(0, 24)} d={d} />)}
        </g>
        <g fill="currentColor">
          {DOTS.map((d) => <circle key={d.k} cx={d.x} cy={d.y} r={d.r} opacity={d.o} />)}
        </g>
      </g>
      <rect width={W} height={H} fill="url(#tunnel-fade)" />
    </svg>
  );
}

/* ------------------------------------------------------------------ tag pile */
const PILE: { label: string; x: number; y: number; r: number }[] = [
  { label: "Internships", x: 0, y: 52, r: -58 },
  { label: "Greenhouse", x: 50, y: 12, r: -14 },
  { label: "Resume", x: 64, y: 70, r: 0 },
  { label: "Cover letters", x: 170, y: 64, r: 178 },
  { label: "Lever", x: 212, y: 18, r: 31 },
  { label: "Ashby", x: 296, y: 4, r: 12 },
  { label: "Startups", x: 330, y: 60, r: -12 },
  { label: "Workday", x: 452, y: 66, r: 2 },
  { label: "Swipe right", x: 450, y: -6, r: -24 },
  { label: "Gmail", x: 560, y: 34, r: 58 },
];

export function TagPile({ className }: { className?: string }) {
  return (
    <div className={cn("pointer-events-none relative h-[118px] w-[640px] select-none", className)} aria-hidden>
      {PILE.map((t) => (
        <span key={t.label} className="tag-pill absolute font-medium"
          style={{ left: t.x, top: t.y, transform: `rotate(${t.r}deg)` }}>
          {t.label}
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ seal */
export function Seal({ className }: { className?: string }) {
  const points = Array.from({ length: 24 }, (_, i) => {
    const r = i % 2 ? 26 : 31;
    const a = (i / 24) * Math.PI * 2;
    return `${round(32 + r * Math.cos(a))},${round(32 + r * Math.sin(a))}`;
  }).join(" ");
  return (
    <svg viewBox="0 0 64 64" className={cn("h-14 w-14 text-primary", className)} aria-hidden>
      <polygon points={points} fill="none" stroke="currentColor" strokeWidth="3" strokeLinejoin="round" />
      <circle cx="32" cy="32" r="4" fill="hsl(var(--foreground))" />
    </svg>
  );
}

/* ------------------------------------------------------------------ headline with brush strokes */
export function BrushHeadline({ lines, className }: { lines: string[]; className?: string }) {
  return (
    <h1 className={cn("display relative isolate text-[clamp(2.3rem,5.1vw,4.6rem)] text-foreground", className)}>
      <span className="brush block">
        {lines.slice(0, 2).map((l) => <span key={l} className="block">{l}</span>)}
      </span>
      {lines.slice(2).map((l) => <span key={l} className="relative block">{l}</span>)}
    </h1>
  );
}
