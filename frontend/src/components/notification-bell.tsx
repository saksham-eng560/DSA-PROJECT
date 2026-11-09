"use client";

import Link from "next/link";
import { useState } from "react";
import { Bell, BellOff, BellRing, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useNotifications } from "@/hooks/use-applications";
import { usePopups } from "@/hooks/use-popups";
import { post } from "@/lib/api-client";
import { cn, timeAgo } from "@/lib/utils";

export function NotificationBell() {
  const { data, mutate } = useNotifications();
  const [open, setOpen] = useState(false);
  const popups = usePopups();

  const markAll = async () => {
    await post("/notifications/read-all");
    mutate();
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative"
          aria-label={`Notifications${data?.unread ? ` (${data.unread} unread)` : ""}${popups.enabled ? "" : ", pop-ups muted"}`}
          title={popups.enabled ? undefined : "Pop-ups muted: notifications still collect here"}>
          {popups.enabled ? <Bell /> : <BellOff />}
          {!!data?.unread && (
            <span className="absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-bold text-primary-foreground">
              {data.unread > 9 ? "9+" : data.unread}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(24rem,calc(100vw-2rem))] p-0">
        <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
          <p className="label-caps">Notifications</p>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" onClick={() => void popups.setEnabled(!popups.enabled)} disabled={!popups.ready}
              loading={popups.saving} aria-pressed={!popups.enabled}
              title={popups.enabled ? "Stop pop-ups; notifications keep collecting here" : "Show pop-ups again"}>
              {!popups.saving && (popups.enabled ? <BellOff /> : <BellRing />)} {popups.enabled ? "Mute pop-ups" : "Unmute"}
            </Button>
            <Button variant="ghost" size="sm" onClick={markAll}><Check /> Mark all read</Button>
          </div>
        </div>
        {!popups.enabled && (
          <p className="border-b bg-muted/40 px-4 py-2 text-xs text-muted-foreground">
            Pop-ups are muted. New notifications still land here and in your other channels.
          </p>
        )}
        <div className="max-h-96 overflow-y-auto scrollbar-thin">
          {!data?.items.length && <p className="p-6 text-center text-sm text-muted-foreground">You&apos;re all caught up.</p>}
          {data?.items.map((n) => (
            <Link key={n.id} href={n.link || "#"}
              onClick={() => { setOpen(false); if (!n.is_read) post(`/notifications/${n.id}/read`).then(() => mutate()); }}
              className={cn("block border-b px-4 py-3 text-sm last:border-0 hover:bg-accent", !n.is_read && "bg-primary/[0.06]")}>
              <div className="flex items-start gap-2">
                {!n.is_read && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary" />}
                <div className="min-w-0">
                  <p className="font-medium">{n.title}</p>
                  {n.body && <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{n.body}</p>}
                  <p className="mt-1 text-[11px] text-muted-foreground">{timeAgo(n.created_at)}</p>
                </div>
              </div>
            </Link>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
