"use client";

import { useState } from "react";
import { MatchHistogram, StatTile, StatusBreakdown, TimelineChart } from "@/components/analytics-charts";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOverview } from "@/hooks/use-applications";
import { PLATFORM_LABELS, cn } from "@/lib/utils";

const RANGES = [
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
  { label: "All time", days: undefined },
];

export default function AnalyticsPage() {
  const [range, setRange] = useState<number | undefined>(undefined);
  const { data, isLoading } = useOverview(range);

  return (
    <div>
      <PageHeader title="Analytics" description="How your search is performing — responses, interviews and what's working." />
      {/* Date range first, one row above everything it scopes */}
      <div className="mb-6 flex flex-wrap gap-2">
        {RANGES.map((r) => (
          <button key={r.label} onClick={() => setRange(r.days)} aria-pressed={range === r.days}
            className={cn("rounded-full border px-3 py-1 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background", range === r.days ? "border-primary bg-primary text-primary-foreground" : "hover:bg-accent")}>
            {r.label}
          </button>
        ))}
      </div>
      {isLoading && !data ? <Skeleton className="h-96" /> : data && (
        <div className={cn("space-y-6", isLoading && "opacity-60")}>
          <div className="grid gap-4 sm:grid-cols-2 sm:[&>:last-child]:col-span-2 xl:grid-cols-5 xl:[&>:last-child]:col-span-1">
            <StatTile label="Applications sent" value={data.totals.applied} hint={`${data.totals.total} tracked in total`} />
            <StatTile label="Response rate" value={`${data.rates.response_rate}%`} hint={`${data.totals.responses} responses`} />
            <StatTile label="Interview rate" value={`${data.rates.interview_rate}%`} hint={`${data.totals.interviews} reached interviews`} />
            <StatTile label="Offer rate" value={`${data.rates.offer_rate}%`} hint={`${data.totals.offers} offers from interviews`} />
            <StatTile label="Time to first response" value={data.rates.avg_days_to_response != null ? `${data.rates.avg_days_to_response} days` : "—"} hint="Average, submitted → first reply" />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Daily activity</CardTitle>
              <CardDescription>Last 30 days</CardDescription>
            </CardHeader>
            <CardContent><TimelineChart data={data.timeline} height={300} /></CardContent>
          </Card>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Match score distribution</CardTitle>
                <CardDescription>Number of jobs per match-score band</CardDescription>
              </CardHeader>
              <CardContent><MatchHistogram data={data.match_distribution} /></CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Pipeline by status</CardTitle>
                <CardDescription>Applications currently in each stage</CardDescription>
              </CardHeader>
              <CardContent><StatusBreakdown byStatus={data.by_status} /></CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Platform effectiveness</CardTitle>
                <CardDescription>Which sources turn into responses and interviews</CardDescription>
              </CardHeader>
              <CardContent>
                {!data.platforms.length ? <p className="text-sm text-muted-foreground">No data yet.</p> : (
                  <table className="w-full text-sm">
                    <thead className="text-xs text-muted-foreground">
                      <tr className="border-b">
                        <th className="py-2 text-left font-medium">Source</th>
                        <th className="py-2 text-right font-medium">Found</th>
                        <th className="py-2 text-right font-medium">Applied</th>
                        <th className="py-2 text-right font-medium">Response</th>
                        <th className="py-2 text-right font-medium">Interview</th>
                      </tr>
                    </thead>
                    <tbody className="tabular-nums">
                      {data.platforms.map((p) => (
                        <tr key={p.platform} className="border-b last:border-0">
                          <td className="py-2">{PLATFORM_LABELS[p.platform] || p.platform}</td>
                          <td className="py-2 text-right">{p.discovered}</td>
                          <td className="py-2 text-right">{p.applied}</td>
                          <td className="py-2 text-right">{p.response_rate}%</td>
                          <td className="py-2 text-right">{p.interview_rate}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Keywords that get callbacks</CardTitle>
                <CardDescription>Job skills most associated with positive responses (needs ≥ 2 applications each)</CardDescription>
              </CardHeader>
              <CardContent>
                {!data.top_keywords.length ? <p className="text-sm text-muted-foreground">Not enough submitted applications yet.</p> : (
                  <table className="w-full text-sm">
                    <thead className="text-xs text-muted-foreground">
                      <tr className="border-b">
                        <th className="py-2 text-left font-medium">Keyword</th>
                        <th className="py-2 text-right font-medium">Applications</th>
                        <th className="py-2 text-right font-medium">Callbacks</th>
                        <th className="py-2 text-right font-medium">Rate</th>
                      </tr>
                    </thead>
                    <tbody className="tabular-nums">
                      {data.top_keywords.map((k) => (
                        <tr key={k.keyword} className="border-b last:border-0">
                          <td className="py-2">{k.keyword}</td>
                          <td className="py-2 text-right">{k.applications}</td>
                          <td className="py-2 text-right">{k.callbacks}</td>
                          <td className="py-2 text-right">{k.callback_rate}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
