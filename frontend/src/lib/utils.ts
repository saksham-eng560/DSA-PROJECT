import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import type { ApplicationStatus } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(value: string | null | undefined, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium" }) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString(undefined, opts);
}

export function formatDateTime(value: string | null | undefined) {
  return formatDate(value, { dateStyle: "medium", timeStyle: "short" });
}

export function timeAgo(value: string | null | undefined) {
  if (!value) return "—";
  const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  const abs = Math.abs(seconds);
  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"], [3600, "minute"], [86400, "hour"], [604800, "day"], [2629800, "week"], [31557600, "month"],
  ];
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (abs < 60) return rtf.format(-seconds, "second");
  for (let i = 1; i < units.length; i++) {
    if (abs < units[i][0]) return rtf.format(-Math.round(seconds / units[i - 1][0]), units[i][1]);
  }
  return rtf.format(-Math.round(seconds / 31557600), "year");
}

export function formatSalary(min: number | null, max: number | null, currency: string | null) {
  if (!min && !max) return null;
  const fmt = new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD", maximumFractionDigits: 0, notation: "compact" });
  if (min && max) return `${fmt.format(min)} – ${fmt.format(max)}`;
  return fmt.format((min || max) as number);
}

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  discovered: "Discovered",
  matched: "Matched",
  skipped: "Skipped",
  preparing: "Preparing",
  pending_approval: "Needs approval",
  approved: "Submitting",
  applied: "Applied",
  acknowledged: "Acknowledged",
  screening: "Screening",
  interview: "Interview",
  assessment: "Assessment",
  final_round: "Final round",
  offer: "Offer",
  accepted: "Accepted",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  failed: "Failed",
};

export const STATUS_TONE: Record<ApplicationStatus, "default" | "info" | "success" | "warning" | "danger" | "muted" | "primary"> = {
  discovered: "muted",
  matched: "info",
  skipped: "muted",
  preparing: "info",
  pending_approval: "warning",
  approved: "primary",
  applied: "primary",
  acknowledged: "info",
  screening: "success",
  interview: "success",
  assessment: "success",
  final_round: "success",
  offer: "success",
  accepted: "success",
  rejected: "danger",
  withdrawn: "muted",
  failed: "danger",
};

export const PLATFORM_LABELS: Record<string, string> = {
  top_companies: "Top companies",
  internshala: "Internshala",
  naukri: "Naukri",
  unstop: "Unstop",
  remote_boards: "Remote boards",
  internships: "Internship lists",
  linkedin: "LinkedIn",
  indeed: "Indeed",
  glassdoor: "Glassdoor",
  wellfound: "Wellfound",
  greenhouse: "Greenhouse",
  lever: "Lever",
  workday: "Workday",
  ashby: "Ashby",
  generic: "Career pages",
  custom: "Company site",
  unknown: "Unknown",
};

export function titleCase(value: string | null | undefined) {
  if (!value) return "";
  return value.replace(/[_-]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
