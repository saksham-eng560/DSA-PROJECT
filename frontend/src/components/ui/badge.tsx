import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/** Outlined pills, like the tag chips of the landing page. */
const badgeVariants = cva(
  "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[11px] font-medium leading-5 transition-colors",
  {
    variants: {
      tone: {
        default: "border-foreground/30 text-foreground",
        primary: "border-primary bg-primary text-primary-foreground",
        info: "border-info/60 text-info",
        success: "border-success/60 text-success",
        warning: "border-warning/70 text-warning",
        danger: "border-primary/70 text-primary",
        muted: "border-border text-muted-foreground",
        outline: "border-foreground/60 text-foreground",
      },
    },
    defaultVariants: { tone: "default" },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

export { badgeVariants };
