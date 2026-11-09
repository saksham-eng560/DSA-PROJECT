"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";
import { LayoutGroup, motion } from "framer-motion";
import {
  BarChart3, Briefcase, CalendarDays, CheckCheck, Crown, FileText, Inbox, Layers, LayoutDashboard, ListChecks, LogOut, Menu, ScrollText,
  Send, Settings, type LucideIcon,
} from "lucide-react";
import { Logo } from "@/components/brand";
import { NotificationBell } from "@/components/notification-bell";
import { ThemeToggle } from "@/components/theme-toggle";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { useAgentStatus, useMe } from "@/hooks/use-applications";
import { applyScanProgress } from "@/hooks/use-scan";
import { useWebSocket } from "@/hooks/use-websocket";
import { ScanIndicator } from "@/components/scan-progress";
import { post } from "@/lib/api-client";
import type { AgentStatus, ScanProgress } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Three or more notifications this close together pop up as one summary that counts up, not a stack. */
const BURST_MS = 10_000;
const BURST_AT = 3;

type NavItem = { href: string; label: string; icon: LucideIcon; badge?: "review" | "pending" };

const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Agent",
    items: [
      { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
      { href: "/dashboard/review", label: "Swipe Review", icon: Layers, badge: "review" },
      { href: "/dashboard/top-companies", label: "Top companies", icon: Crown },
      { href: "/dashboard/submit", label: "Ready to submit", icon: ListChecks, badge: "pending" },
      { href: "/dashboard/applications", label: "Applications", icon: Send },
      { href: "/dashboard/applied", label: "I Applied", icon: CheckCheck },
      { href: "/dashboard/jobs", label: "All jobs", icon: Briefcase },
    ],
  },
  {
    section: "Inbox",
    items: [
      { href: "/dashboard/emails", label: "Emails", icon: Inbox },
      { href: "/dashboard/interviews", label: "Interviews", icon: CalendarDays },
    ],
  },
  {
    section: "Insights",
    items: [
      { href: "/dashboard/analytics", label: "Analytics", icon: BarChart3 },
      { href: "/dashboard/logs", label: "Agent logs", icon: ScrollText },
    ],
  },
  {
    section: "Setup",
    items: [
      { href: "/dashboard/resume", label: "Resume Lab", icon: FileText },
      { href: "/dashboard/settings", label: "Settings", icon: Settings },
    ],
  },
];

function initials(name: string | undefined) {
  return (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("") || "?";
}

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: me, isLoading } = useMe();
  const { data: status } = useAgentStatus();
  const { mutate } = useSWRConfig();
  const toast = useToast();
  const [mobileOpen, setMobileOpen] = useState(false);
  const recent = useRef<number[]>([]);

  useEffect(() => {
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  }, []);

  const live = useWebSocket((event) => {
    if (event.type === "notification") {
      const d = event.data as { title?: string; body?: string; event_type?: string; link?: string };
      mutate((key) => typeof key === "string" && (key.startsWith("/notifications") || key.startsWith("/agent")));
      if (me?.preferences.notification_popups === false) return; // muted: it's in the bell, no pop-up
      const now = Date.now();
      recent.current = [...recent.current.filter((t) => now - t < BURST_MS), now];
      const burst = recent.current.length >= BURST_AT;
      const title = burst ? `${recent.current.length} new notifications` : d.title || "Update";
      const body = burst ? "They're all in the bell (top right). Mute pop-ups there if you'd rather not see them." : d.body;
      if (burst) toast({ id: "notification-burst", title, description: body, tone: "info" });
      else toast({ title, description: body, tone: d.event_type?.includes("error") || d.event_type?.includes("failed") ? "error" : "info",
        action: d.link?.startsWith("/") ? { label: "Open", onClick: () => router.push(d.link as string) } : undefined });
      if (document.hidden && "Notification" in window && Notification.permission === "granted") {
        navigator.serviceWorker?.controller?.postMessage({ type: "notify", title, body, link: burst ? "/dashboard" : d.link, tag: burst ? "burst" : undefined });
      }
    }
    if (event.type === "scan_progress") {
      const d = event.data as { run_id: string; progress: ScanProgress };
      let known = true;
      void mutate<AgentStatus>("/agent/status", (current) => {
        const next = applyScanProgress(current, d.run_id, d.progress);
        known = next !== current;
        return next;
      }, { revalidate: false }).then(() => {
        if (!known) void mutate("/agent/status"); // a scan this page hasn't seen yet: fetch it
      });
      return;
    }
    if (event.type === "application_updated" || event.type === "agent_run_updated") {
      mutate((key) => typeof key === "string" && ["/applications", "/agent", "/analytics", "/jobs", "/review"].some((p) => key.startsWith(p)));
    }
  });

  const logout = async () => {
    await post("/auth/logout");
    router.replace("/login");
  };

  const current = NAV.flatMap((s) => s.items).find((i) => (i.href === "/dashboard" ? pathname === i.href : pathname.startsWith(i.href)));

  const badgeFor = (badge?: NavItem["badge"]) => {
    if (badge === "review") return status?.to_review || 0;
    if (badge === "pending") return status?.pending_approval || 0;
    return 0;
  };

  const renderNav = (scope: string) => (
    <LayoutGroup id={scope}>
    <nav className="flex flex-1 flex-col gap-6 overflow-y-auto py-6 scrollbar-thin" aria-label="Dashboard">
      {NAV.map(({ section, items }) => (
        <div key={section}>
          <p className="label-caps mb-2 px-6 text-[10px] text-muted-foreground">{section}</p>
          {items.map(({ href, label, icon: Icon, badge }) => {
            const active = href === "/dashboard" ? pathname === href : pathname.startsWith(href);
            const count = badgeFor(badge);
            return (
              <Link key={href} href={href} onClick={() => setMobileOpen(false)} aria-current={active ? "page" : undefined}
                className={cn("group relative flex h-11 items-center gap-3 px-6 text-[12px] font-semibold uppercase tracking-[0.12em] transition-colors",
                  active ? "text-primary-foreground" : "text-foreground/75 hover:bg-accent hover:text-foreground")}>
                {/* The red highlight slides from the old item to the new one */}
                {active && <motion.span layoutId="nav-active" aria-hidden className="absolute inset-0 bg-primary"
                  transition={{ type: "spring", stiffness: 520, damping: 42 }} />}
                <Icon className="relative h-4 w-4 shrink-0 transition-transform duration-200 group-hover:translate-x-0.5" />
                <span className="relative flex-1 truncate">{label}</span>
                {count > 0 && (
                  <span className={cn("relative min-w-6 rounded-full border px-1.5 text-center text-[10px] leading-5 tabular-nums",
                    active ? "border-primary-foreground/70" : badge === "review" ? "border-primary bg-primary text-primary-foreground" : "border-warning text-warning")}>
                    {count > 999 ? "999+" : count}
                  </span>
                )}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
    </LayoutGroup>
  );

  const footer = (
    <div className="border-t border-line/60 px-6 py-4 text-xs text-muted-foreground">
      <div className="flex items-center justify-between">
        <span className="label-caps text-[10px]">Applied today</span>
        <span className="tabular-nums text-foreground">{status?.applied_today ?? 0} / {status?.daily_limit ?? "—"}</span>
      </div>
      <div className="mt-2 h-1 bg-foreground/10">
        <div className="h-full bg-primary transition-all"
          style={{ width: `${Math.min(100, ((status?.applied_today ?? 0) / Math.max(1, status?.daily_limit ?? 1)) * 100)}%` }} />
      </div>
      <p className="mt-3 flex items-center gap-2">
        <span className={cn("h-2 w-2 rounded-full", live ? "animate-pulse-dot bg-success" : "bg-muted-foreground/50")} />
        {live ? "Live updates on" : "Polling for updates"}
      </p>
    </div>
  );

  return (
    <div className="noise flex min-h-screen bg-background">
      <aside className="sticky top-0 hidden h-screen w-[248px] shrink-0 flex-col border-r border-line/60 lg:flex">
        <div className="flex h-16 items-center border-b border-line/60 px-6"><Logo href="/dashboard" /></div>
        {renderNav("desktop-nav")}
        {footer}
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="flex w-[272px] flex-col gap-0 border-line/60 p-0">
          <SheetTitle className="flex h-16 items-center border-b border-line/60 px-6"><Logo href="/dashboard" /></SheetTitle>
          <SheetDescription className="sr-only">Dashboard navigation</SheetDescription>
          {renderNav("mobile-nav")}
          {footer}
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 items-center gap-2 border-b border-line/60 bg-background/90 px-4 backdrop-blur lg:px-8">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu"><Menu /></Button>
          <p className="label-caps hidden text-muted-foreground sm:block">
            <span className="text-primary">/</span> {current?.label || "Dashboard"}
          </p>
          <div className="flex-1" />
          <ScanIndicator run={status?.running_runs.find((r) => r.run_type === "scan") ?? null} />
          {!!status?.to_review && !pathname.startsWith("/dashboard/review") && (
            <Link href="/dashboard/review"
              className="label-caps mr-2 hidden items-center gap-2 border border-primary px-3 py-1.5 text-[11px] text-primary transition-colors hover:bg-primary hover:text-primary-foreground md:inline-flex">
              <Layers className="h-3.5 w-3.5" /> {status.to_review} to swipe
            </Link>
          )}
          <NotificationBell />
          <ThemeToggle />
          <DropdownMenu>
            <DropdownMenuTrigger className="ml-1 flex items-center gap-3 px-1 py-1 outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="Account menu">
              <div className="hidden text-right text-sm sm:block">
                {isLoading ? <Skeleton className="h-4 w-28" /> : <p className="font-medium leading-tight">{me?.full_name}</p>}
                <p className="text-xs text-muted-foreground">{me?.email}</p>
              </div>
              <Avatar className="h-9 w-9 rounded-none border border-line">
                <AvatarFallback className="rounded-none bg-primary text-xs font-bold text-primary-foreground">{initials(me?.full_name)}</AvatarFallback>
              </Avatar>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel className="font-normal">
                <p className="font-medium">{me?.full_name}</p>
                <p className="text-xs text-muted-foreground">{me?.email}</p>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => router.push("/dashboard/settings")}><Settings /> Settings</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => router.push("/dashboard/resume")}><FileText /> Resume Lab</DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={logout} className="text-primary focus:text-primary"><LogOut /> Sign out</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </header>
        <main className="flex-1 p-4 sm:p-6 lg:p-10">{children}</main>
      </div>
    </div>
  );
}
