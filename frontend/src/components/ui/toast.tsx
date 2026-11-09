"use client";

import * as React from "react";
import { toast as sonner } from "sonner";
import { Toaster } from "@/components/ui/sonner";

/** `id`: a toast with the same id is updated in place instead of stacking a new one.
 *  `action`: a button on the toast (e.g. "View" to open what it's about). */
type ToastInput = {
  title: string;
  description?: string;
  tone?: "success" | "error" | "info";
  id?: string;
  action?: { label: string; onClick: () => void };
};

/** App-wide toasts (sonner). Kept as a hook so pages call `toast({ title, description, tone })`. */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  return (
    <>
      {children}
      <Toaster position="bottom-right" />
    </>
  );
}

function show({ title, description, tone, id, action }: ToastInput) {
  const fn = tone === "error" ? sonner.error : tone === "success" ? sonner.success : sonner.message;
  fn(title, { description, id, action });
}

/** Clear every toast on screen (muting pop-ups clears the ones already showing). */
export const dismissToasts = () => sonner.dismiss();

export function useToast() {
  return show;
}
