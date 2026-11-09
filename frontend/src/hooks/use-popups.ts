"use client";

import { useState } from "react";
import { dismissToasts } from "@/components/ui/toast";
import { useMe } from "@/hooks/use-applications";
import { put } from "@/lib/api-client";

/** Notification pop-ups on or off (saved to your account, so every device follows it). Muted, notifications
 *  still collect in the bell; they just don't pop up in the dashboard or as system notifications. */
export function usePopups() {
  const { data: me, mutate } = useMe();
  const [saving, setSaving] = useState(false);
  const enabled = me?.preferences.notification_popups !== false;

  const setEnabled = async (on: boolean) => {
    if (!me) return;
    if (!on) dismissToasts(); // silence now, not after the current pop-ups time out
    setSaving(true);
    try {
      const next = { ...me, preferences: { ...me.preferences, notification_popups: on } };
      await mutate(async () => {
        await put("/users/me/preferences", { preferences: { notification_popups: on } });
        return next;
      }, { optimisticData: next, rollbackOnError: true, revalidate: true });
    } finally {
      setSaving(false);
    }
  };

  return { enabled, setEnabled, saving, ready: !!me };
}
