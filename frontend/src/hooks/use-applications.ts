"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api-client";
import type {
  AgentStatus,
  ApplicationDetail,
  ApplicationSummary,
  Integrations,
  NotificationItem,
  Overview,
  Paginated,
  SubmitQueue,
  User,
} from "@/lib/types";

const LIVE = { refreshInterval: 15000, revalidateOnFocus: true };

/** Poll every 1.5 s while a scan runs (the WebSocket usually beats it), every 15 s otherwise. */
const scanning = (status?: AgentStatus) => !!status?.running_runs.some((r) => r.run_type === "scan");

export function useMe() {
  return useSWR<User>("/auth/me", fetcher);
}

export function useAgentStatus() {
  return useSWR<AgentStatus>("/agent/status", fetcher, {
    ...LIVE, refreshInterval: (status?: AgentStatus) => (scanning(status) ? 1500 : LIVE.refreshInterval),
  });
}

export function useOverview(days?: number) {
  return useSWR<Overview>(`/analytics/overview${days ? `?days=${days}` : ""}`, fetcher, LIVE);
}

export function useApplications(params: Record<string, string | number | boolean | undefined>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== "") query.set(k, String(v));
  });
  return useSWR<Paginated<ApplicationSummary>>(`/applications?${query.toString()}`, fetcher, LIVE);
}

export function useApplication(id: string | undefined) {
  return useSWR<ApplicationDetail>(id ? `/applications/${id}` : null, fetcher, LIVE);
}

/** "Ready to submit": filled-in applications paused for you, each as a review sheet. No refetch on focus:
 *  coming back from the posting in another tab must not shuffle the sheet you're halfway through. */
export function useSubmitQueue() {
  return useSWR<SubmitQueue>("/applications/review-queue?limit=100", fetcher, { refreshInterval: 30000, revalidateOnFocus: false });
}

export function useNotifications() {
  return useSWR<{ items: NotificationItem[]; unread: number }>("/notifications?limit=30", fetcher, { refreshInterval: 30000 });
}

export function useIntegrations() {
  return useSWR<Integrations>("/users/me/integrations", fetcher);
}
