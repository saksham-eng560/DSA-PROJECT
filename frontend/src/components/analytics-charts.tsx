"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TooltipProps } from "recharts";
import { MaybeAnimatedNumber } from "@/components/motion";
import { Button } from "@/components/ui/button";
import type { Overview } from "@/lib/types";
import { STATUS_LABELS, cn } from "@/lib/utils";
import type { ApplicationStatus } from "@/lib/types";

const AXIS = { stroke: "var(--chart-axis)", tick: { fill: "var(--chart-muted)", fontSize: 12 }, tickLine: false };

const SERIES = [
  { key: "discovered", label: "Discovered", color: "var(--series-1)" },
  { key: "applied", label: "Applied", color: "var(--series-2)" },
  { key: "responses", label: "Responses", color: "var(--series-3)" },
] as const;

function shortDate(value: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Legend: rendered in text tokens, keyed by a short line in the series color. */
function LineLegend() {
  return (
    <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
      {SERIES.map((s) => (
        <span key={s.key} className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded-full" style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

function TimelineTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  return (
    <div className="border border-line bg-popover px-3 py-2 text-xs shadow-xl">
      <p className="mb-1 text-muted-foreground">{shortDate(String(label))}</p>
      {SERIES.map((s) => {
        const item = payload.find((p) => p.dataKey === s.key);
        return (
          <div key={s.key} className="flex items-center gap-2">
            <span className="h-0.5 w-3 rounded-full" style={{ background: s.color }} />
            <span className="font-semibold tabular-nums text-foreground">{item?.value ?? 0}</span>
            <span className="text-muted-foreground">{s.label}</span>
          </div>
        );
      })}
    </div>
  );
}

export function TimelineChart({ data, height = 260 }: { data: Overview["timeline"]; height?: number }) {
  const [table, setTable] = useState(false);
  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-2">
        <LineLegend />
        <Button variant="ghost" size="sm" onClick={() => setTable((t) => !t)}>{table ? "Show chart" : "Show table"}</Button>
      </div>
      {table ? (
        <div className="max-h-[260px] overflow-y-auto border">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-muted text-xs text-muted-foreground">
              <tr><th className="px-3 py-1.5 text-left font-medium">Date</th>{SERIES.map((s) => <th key={s.key} className="px-3 py-1.5 text-right font-medium">{s.label}</th>)}</tr>
            </thead>
            <tbody className="tabular-nums">
              {[...data].reverse().map((row) => (
                <tr key={row.date} className="border-t">
                  <td className="px-3 py-1.5">{shortDate(row.date)}</td>
                  {SERIES.map((s) => <td key={s.key} className="px-3 py-1.5 text-right">{row[s.key]}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          <LineChart data={data} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
            <XAxis dataKey="date" tickFormatter={shortDate} {...AXIS} minTickGap={24} />
            <YAxis allowDecimals={false} {...AXIS} axisLine={false} width={48} />
            <Tooltip content={<TimelineTooltip />} cursor={{ stroke: "var(--chart-axis)", strokeWidth: 1 }} />
            {SERIES.map((s) => (
              <Line key={s.key} type="linear" dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2}
                strokeLinecap="round" strokeLinejoin="round" dot={false}
                activeDot={{ r: 4, fill: s.color, stroke: "hsl(var(--card))", strokeWidth: 2 }} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function CountTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  return (
    <div className="border border-line bg-popover px-3 py-2 text-xs shadow-xl">
      <span className="font-semibold tabular-nums text-foreground">{payload[0].value}</span>{" "}
      <span className="text-muted-foreground">jobs scored {label}</span>
    </div>
  );
}

/** Single-series histogram: one hue, capped bar width, 4px rounded data-end. */
export function MatchHistogram({ data, height = 220 }: { data: Overview["match_distribution"]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }} barCategoryGap={4}>
        <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
        <XAxis dataKey="range" {...AXIS} interval={0} fontSize={11} />
        <YAxis allowDecimals={false} {...AXIS} axisLine={false} width={48} />
        <Tooltip content={<CountTooltip />} cursor={{ fill: "hsl(var(--muted))" }} />
        <Bar dataKey="count" fill="var(--series-1)" maxBarSize={24} radius={[0, 0, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

const PIPELINE: ApplicationStatus[] = [
  "matched", "preparing", "pending_approval", "approved", "applied", "acknowledged", "screening", "assessment",
  "interview", "final_round", "offer", "accepted", "rejected", "withdrawn", "failed", "skipped",
];

/** Many classes -> a single-hue bar list with labels in text tokens and values at the bar tip. */
export function StatusBreakdown({ byStatus }: { byStatus: Record<string, number> }) {
  const rows = PIPELINE.map((s) => ({ status: s, count: byStatus[s] || 0 })).filter((r) => r.count > 0);
  const max = Math.max(1, ...rows.map((r) => r.count));
  if (!rows.length) return <p className="text-sm text-muted-foreground">No applications yet.</p>;
  return (
    <ul className="space-y-2">
      {rows.map((r) => (
        <li key={r.status} className="grid grid-cols-[8rem_1fr] items-center gap-3 text-sm" title={`${STATUS_LABELS[r.status]}: ${r.count}`}>
          <span className="truncate text-muted-foreground">{STATUS_LABELS[r.status]}</span>
          <div className="flex items-center gap-2">
            <div className="h-3" style={{ width: `${(r.count / max) * 85}%`, minWidth: 4, background: "var(--series-1)" }} />
            <span className="tabular-nums text-xs font-medium">{r.count}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function StatTile({ label, value, hint, className, icon }: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  className?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className={cn("flex flex-col border bg-card p-5", className)}>
      <div className="flex items-center justify-between gap-2 text-muted-foreground">
        <span className="label-caps text-[10px]">{label}</span>
        {icon}
      </div>
      <p className="mt-4 font-display text-4xl leading-none"><MaybeAnimatedNumber value={value} /></p>
      {hint && <p className="mt-3 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
