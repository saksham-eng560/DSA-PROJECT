"use client";

import * as React from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

/** Convenience wrapper around the shadcn/Radix dialog: title, scrollable body and a footer bar. */
export function Modal({ open, onOpenChange, title, description, children, footer, className }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  children?: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
}) {
  // These dialogs are opened from state, not a DialogTrigger, so Radix has no trigger to hand focus back to on
  // close. Remember whatever had focus when the dialog opened and restore it (keyboard users keep their place).
  const opener = React.useRef<HTMLElement | null>(null);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn("flex max-h-[90vh] max-w-lg flex-col gap-0 p-0", className)}
        onOpenAutoFocus={() => { opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; }}
        onCloseAutoFocus={(e) => {
          if (opener.current?.isConnected) {
            e.preventDefault();
            opener.current.focus();
          }
        }}>
        <DialogHeader className="border-b p-5 pr-12 text-left">
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : <DialogDescription className="sr-only">{title}</DialogDescription>}
        </DialogHeader>
        <div className="overflow-y-auto p-5 scrollbar-thin">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t p-4">{footer}</div>}
      </DialogContent>
    </Dialog>
  );
}
