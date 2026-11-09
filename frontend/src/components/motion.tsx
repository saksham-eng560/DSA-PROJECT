"use client";

/**
 * Motion primitives shared by the dashboard: route transitions, staggered reveals and counters.
 * Everything honours "reduce motion" (MotionConfig reducedMotion="user" + useReducedMotion).
 */

import { useEffect, useRef, useState } from "react";
import { animate, motion, useInView, useReducedMotion, type Variants } from "framer-motion";
import { cn } from "@/lib/utils";

export const EASE_OUT = [0.22, 1, 0.36, 1] as const;

/** Content fades and rises in on every route change (used by app/**\/template.tsx). */
export function PageTransition({ children, className }: { children: React.ReactNode; className?: string }) {
  const reduce = useReducedMotion();
  return (
    <>
      <RouteSweep />
      <motion.div
        className={className}
        initial={reduce ? false : { opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.42, ease: EASE_OUT }}
      >
        {children}
      </motion.div>
    </>
  );
}

/** A thin signal-red bar that sweeps across the top of the window when a page opens. */
export function RouteSweep() {
  const reduce = useReducedMotion();
  if (reduce) return null;
  return (
    <motion.span
      aria-hidden
      className="pointer-events-none fixed inset-x-0 top-0 z-[60] block h-[3px] origin-left bg-primary"
      initial={{ scaleX: 0, opacity: 1 }}
      animate={{ scaleX: [0, 0.72, 1], opacity: [1, 1, 0] }}
      transition={{ duration: 0.75, times: [0, 0.55, 1], ease: "easeOut" }}
    />
  );
}

const container: Variants = {
  hidden: {},
  show: (delay: number = 0) => ({ transition: { staggerChildren: 0.05, delayChildren: delay } }),
};
const item: Variants = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.38, ease: EASE_OUT } },
};

type Tag = "div" | "ul" | "ol" | "section";
type ItemTag = "div" | "li" | "article";

/** Children wrapped in <StaggerItem> reveal one after another. */
export function Stagger({ children, className, delay = 0, as = "div" }: {
  children: React.ReactNode; className?: string; delay?: number; as?: Tag;
}) {
  const reduce = useReducedMotion();
  const Component = motion[as];
  return (
    <Component className={className} variants={container} custom={delay} initial={reduce ? false : "hidden"} animate="show">
      {children}
    </Component>
  );
}

export function StaggerItem({ children, className, as = "div" }: { children: React.ReactNode; className?: string; as?: ItemTag }) {
  const Component = motion[as];
  return <Component className={className} variants={item}>{children}</Component>;
}

/** Counts up to `value` when it scrolls into view (and animates between later values).
 *  Proportional figures by default; pass `tabular-nums` in className for numbers in columns. */
export function AnimatedNumber({ value, format = (n) => n.toLocaleString(), className, duration = 0.9 }: {
  value: number; format?: (n: number) => string; className?: string; duration?: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-40px" });
  const reduce = useReducedMotion();
  const from = useRef(0);
  const [text, setText] = useState(() => format(0));

  useEffect(() => {
    if (!inView) return;
    if (reduce) {
      setText(format(value));
      from.current = value;
      return;
    }
    const controls = animate(from.current, value, {
      duration, ease: EASE_OUT, onUpdate: (v) => setText(format(Math.round(v))),
    });
    from.current = value;
    return () => controls.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- format is usually an inline arrow
  }, [value, inView, reduce, duration]);

  return <span ref={ref} className={className}>{text}</span>;
}

/** Numbers animate, anything else ("12%", "—") is shown as is. */
export function MaybeAnimatedNumber({ value, className }: { value: React.ReactNode; className?: string }) {
  if (typeof value === "number") return <AnimatedNumber value={value} className={className} />;
  const match = typeof value === "string" ? /^(\d+)(%?)$/.exec(value) : null;
  if (match) return <AnimatedNumber value={Number(match[1])} format={(n) => `${n.toLocaleString()}${match[2]}`} className={className} />;
  return <span className={className}>{value}</span>;
}
