"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion, type Variants } from "framer-motion";
import {
  AlertTriangle, ArrowUpRight, Bot, Building2, Check, CheckCheck, Copy, FileText, Layers, ListChecks, MapPin, Send, SkipForward,
  Trash2, Undo2, X, ZoomIn,
} from "lucide-react";
import { CompanyBadge } from "@/components/company-badge";
import { EmptyState } from "@/components/empty-state";
import { IAppliedButton } from "@/components/i-applied-button";
import { Modal } from "@/components/modal";
import { EASE_OUT } from "@/components/motion";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { useAgentStatus, useSubmitQueue } from "@/hooks/use-applications";
import { ApiError, post } from "@/lib/api-client";
import type { BotMissing, DirectSubmitResponse, ReviewRow, SubmitQueueItem } from "@/lib/types";
import { PLATFORM_LABELS, cn, timeAgo, titleCase } from "@/lib/utils";

type RowStatus = "pending" | "editing" | "confirmed";
type RowState = { value: string; status: RowStatus; edited: boolean; error?: string; before?: RowStatus };
type Sheet = Record<string, RowState>;
type Exit = "submit" | "skip";

const EDITABLE: ReviewRow["kind"][] = ["profile", "question", "cover_letter"];
const editable = (row: ReviewRow) => EDITABLE.includes(row.kind);
/** Required and still empty: it can't be confirmed until you fill it in (uploads are informational). */
const blocksConfirm = (row: ReviewRow, value: string) => row.required && !value.trim() && editable(row);
const needsYou = (row: ReviewRow, value: string) => row.flagged || blocksConfirm(row, value);
const longText = (row: ReviewRow, value: string) => row.kind === "cover_letter" || row.type === "textarea" || value.length > 120;
const fresh = (row: ReviewRow): RowState => ({ value: row.value, status: "pending", edited: false });

/** The one step that gets the Internshala bot going (Settings › Integrations has the switch and the sync steps). */
const BOT_FIX: Record<BotMissing, string> = {
  bot_off: "Turn on the bot",
  not_synced: "Sync your Internshala login",
  expired: "Sync your Internshala login again",
};
const INTEGRATIONS = "/dashboard/settings?tab=integrations";

function isTyping(target: EventTarget | null) {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

/** "internshala.com" -> "Internshala", "jobs.lever.co" -> "Lever". */
function siteName(item: SubmitQueueItem) {
  try {
    const parts = new URL(item.apply_url || "").hostname.split(".");
    let name = parts.length >= 2 ? parts[parts.length - 2] : parts[0];
    if (["co", "com", "org", "net", "ac", "gov"].includes(name) && parts.length >= 3) name = parts[parts.length - 3];
    return name ? name.charAt(0).toUpperCase() + name.slice(1) : "the site";
  } catch {
    return "the site";
  }
}

function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="border px-1.5 py-0.5 font-mono text-[11px] text-foreground">{children}</kbd>;
}

/** The inline editor a ✗ Fix opens: a dropdown for choices, a text box for long answers, an input otherwise. */
function RowEditor({ row, state, onSave, onCancel }: {
  row: ReviewRow; state: RowState; onSave: (value: string) => void; onCancel: () => void;
}) {
  const [draft, setDraft] = useState(state.value);
  const id = `fix-${row.key}`;
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); onCancel(); }
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey || ["INPUT", "SELECT"].includes(e.currentTarget.tagName))) {
      e.preventDefault();
      onSave(draft);
    }
  };
  return (
    <div className="mt-3 space-y-3">
      {row.options.length > 0 ? (
        <Select id={id} aria-label={row.label} value={draft} autoFocus onChange={(e) => setDraft(e.target.value)} onKeyDown={keys}>
          {!row.options.includes(draft) && <option value={draft}>{draft || "Select…"}</option>}
          {row.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </Select>
      ) : longText(row, draft) ? (
        <Textarea id={id} aria-label={row.label} value={draft} autoFocus onChange={(e) => setDraft(e.target.value)} onKeyDown={keys}
          className={cn(row.kind === "cover_letter" ? "min-h-[320px] font-serif text-[15px]" : "min-h-[120px]")} />
      ) : (
        <Input id={id} aria-label={row.label} value={draft} autoFocus onChange={(e) => setDraft(e.target.value)} onKeyDown={keys}
          type={row.type === "date" && (!draft || /^\d{4}-\d{2}-\d{2}$/.test(draft)) ? "date" : "text"}
          inputMode={row.type === "email" ? "email" : row.type === "tel" ? "tel" : row.type === "number" ? "numeric" : undefined} />
      )}
      {state.error && <p className="text-xs text-primary" role="alert">{state.error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => onSave(draft)}><Check /> Save</Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
        <span className="text-xs text-muted-foreground">
          {longText(row, draft) && !row.options.length ? <><Kbd>Ctrl</Kbd> + <Kbd>Enter</Kbd> saves</> : <><Kbd>Enter</Kbd> saves</>} · <Kbd>Esc</Kbd> cancels
        </span>
      </div>
    </div>
  );
}

/** For postings you apply to yourself: copy each prepared answer over to the site. */
function CopyButton({ row, value }: { row: ReviewRow; value: string }) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);
  const copy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      toast({ title: "Couldn't copy", description: "Your browser blocked the clipboard. Select the text and copy it instead.", tone: "error" });
    }
  };
  return (
    <Button size="sm" variant="outline" className="col-span-2 sm:col-span-1" onClick={copy} disabled={!value.trim()} aria-label={`Copy: ${row.label}`}>
      {copied ? <><Check /> Copied</> : <><Copy /> Copy</>}
    </Button>
  );
}

/** One prefilled item: ✓ Correct or ✗ Fix; confirmed rows fold into a quiet line with a check.
 *  `readOnly` (a posting you apply to yourself): just the value and a Copy button. */
function SheetRow({ row, state, focused, readOnly, rowRef, onFocus, onConfirm, onFix, onSave, onCancel, onUndo }: {
  row: ReviewRow;
  state: RowState;
  focused: boolean;
  readOnly: boolean;
  rowRef: (el: HTMLLIElement | null) => void;
  onFocus: () => void;
  onConfirm: () => void;
  onFix: () => void;
  onSave: (value: string) => void;
  onCancel: () => void;
  onUndo: () => void;
}) {
  const reduce = useReducedMotion();
  const attention = needsYou(row, state.value) && state.status !== "confirmed";
  const empty = !state.value.trim();
  const blocked = blocksConfirm(row, state.value);
  return (
    <li ref={rowRef} tabIndex={-1} onClick={onFocus} aria-label={row.label}
      className={cn("relative scroll-mb-40 scroll-mt-24 px-5 py-4 outline-none transition-colors sm:px-6",
        attention && "bg-warning/[0.07]", focused && "bg-accent/70")}>
      {(focused || attention) && <span aria-hidden className={cn("absolute inset-y-0 left-0 w-[3px]", focused ? "bg-primary" : "bg-warning")} />}

      {state.status === "confirmed" ? (
        <div className="flex items-center gap-3">
          <motion.span initial={reduce ? false : { scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.22, ease: EASE_OUT }}
            className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-success text-success-foreground">
            <Check className="h-3 w-3" strokeWidth={3} />
          </motion.span>
          <p className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
            <span>{row.label}</span>
            <span className="ml-2 text-foreground/80">{row.kind === "cover_letter" ? `${state.value.trim().split(/\s+/).filter(Boolean).length} words` : state.value || "Left blank"}</span>
          </p>
          {state.edited && <Badge tone="outline" className="hidden sm:inline-flex">edited</Badge>}
          <Button variant="ghost" size="sm" className="shrink-0 px-2" onClick={(e) => { e.stopPropagation(); onUndo(); }} aria-label={`Undo: ${row.label}`}>
            <Undo2 /> <span className="hidden sm:inline">Undo</span>
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-[13px] font-semibold leading-snug">
                {row.label}{row.required && <span className="text-primary" aria-label="required"> *</span>}
              </p>
              {row.kind === "profile" && <Badge tone="muted">profile</Badge>}
              {row.flagged && row.confidence != null && <Badge tone="warning">{Math.round(row.confidence * 100)}% sure</Badge>}
              {state.edited && <Badge tone="outline">edited</Badge>}
            </div>
            {state.status === "editing" ? (
              <RowEditor row={row} state={state} onSave={onSave} onCancel={onCancel} />
            ) : (
              <>
                {row.kind === "resume" || row.kind === "file" ? (
                  <p className="mt-1.5 flex min-w-0 items-center gap-2 text-sm">
                    <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="truncate">{state.value || "Nothing uploaded"}</span>
                  </p>
                ) : empty ? (
                  <p className="mt-1.5 text-sm italic text-muted-foreground">{row.required ? "Empty: needs your answer" : "Left blank"}</p>
                ) : (
                  <p className={cn("mt-1.5 whitespace-pre-line break-words text-sm text-foreground/90",
                    row.kind === "cover_letter" ? "line-clamp-4 font-serif text-[15px] leading-relaxed" : "line-clamp-3")}>
                    {state.value}
                  </p>
                )}
                {row.note && !state.edited && (
                  <p className="mt-1.5 flex items-start gap-1.5 text-xs text-warning">
                    <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />{row.note}
                  </p>
                )}
                {row.kind === "file" && <p className="mt-1 text-xs text-muted-foreground">Uploaded by the agent. Shown for your information.</p>}
              </>
            )}
          </div>
          {readOnly ? (
            <div className="grid shrink-0 grid-cols-2 gap-2 sm:flex">
              {row.url ? (
                <a href={row.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
                  className={cn(buttonVariants({ variant: "outline", size: "sm" }), "col-span-2 sm:col-span-1")}>
                  Open PDF <ArrowUpRight />
                </a>
              ) : editable(row) && <CopyButton row={row} value={state.value} />}
            </div>
          ) : state.status !== "editing" && (
            <div className="grid shrink-0 grid-cols-2 gap-2 sm:flex">
              <Button size="sm" variant="outline" disabled={blocked}
                title={blocked ? "Fill this in first" : "Correct (Y)"} aria-label={`Correct: ${row.label}`}
                onClick={(e) => { e.stopPropagation(); onConfirm(); }}>
                <Check /> Correct
              </Button>
              {editable(row) ? (
                <Button size="sm" variant={blocked ? "default" : "ghost"} className={cn(!blocked && "border border-line/70")}
                  title="Fix (N)" aria-label={`Fix: ${row.label}`} onClick={(e) => { e.stopPropagation(); onFix(); }}>
                  <X /> Fix
                </Button>
              ) : row.url ? (
                <a href={row.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
                  className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "border border-line/70")}>
                  Open PDF <ArrowUpRight />
                </a>
              ) : <span />}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function SubmitInner() {
  const params = useSearchParams();
  const toast = useToast();
  const reduce = useReducedMotion();
  const { data, isLoading, mutate } = useSubmitQueue();
  const { data: status, mutate: refreshStatus } = useAgentStatus();
  const [currentId, setCurrentId] = useState<string | null>(() => params.get("id"));
  const [done, setDone] = useState<Set<string>>(new Set()); // submitted / applied here: gone before the refetch lands
  const [sheets, setSheets] = useState<Record<string, Sheet>>({});
  const [focus, setFocus] = useState(0);
  const [exit, setExit] = useState<Exit>("skip");
  const [busy, setBusy] = useState(false);
  const [zoom, setZoom] = useState(false);
  const askedFor = useRef(params.get("id"));
  const rowRefs = useRef<(HTMLLIElement | null)[]>([]);

  const items = useMemo(() => (data?.items || []).filter((i) => !done.has(i.id)), [data?.items, done]);
  const found = items.findIndex((i) => i.id === currentId);
  const index = found < 0 ? 0 : found;
  const item: SubmitQueueItem | undefined = items[index];
  const rows = useMemo(() => item?.rows ?? [], [item]);
  const sheet = useMemo(() => (item && sheets[item.id]) || {}, [item, sheets]);
  const stateOf = useCallback((row: ReviewRow) => sheet[row.key] ?? fresh(row), [sheet]);
  const count = data && data.total > data.items.length ? Math.max(items.length, data.total - done.size) : items.length;

  // Start at ?id= (from "Review & submit" buttons); say so when it isn't waiting any more.
  useEffect(() => {
    if (!data || !askedFor.current) return;
    if (!data.items.some((i) => i.id === askedFor.current)) {
      toast({ title: "That application isn't waiting for you anymore",
        description: data.items.length ? "Showing the next one in the queue." : undefined, tone: "info" });
      if (!data.items.length) window.history.replaceState(null, "", window.location.pathname);
    }
    askedFor.current = null;
  }, [data, toast]);

  // Keep the address on the application you're looking at, so a reload comes back to it.
  useEffect(() => {
    if (!item) return;
    if (item.id !== currentId) setCurrentId(item.id);
    if (new URLSearchParams(window.location.search).get("id") !== item.id) {
      window.history.replaceState(null, "", `/dashboard/submit?id=${item.id}`);
    }
  }, [item, currentId]);

  const update = useCallback((row: ReviewRow, patch: Partial<RowState>) => {
    if (!item) return;
    setSheets((prev) => {
      const current = prev[item.id] || {};
      return { ...prev, [item.id]: { ...current, [row.key]: { ...(current[row.key] ?? fresh(row)), ...patch } } };
    });
  }, [item]);

  const focusRow = useCallback((i: number) => {
    if (!rows.length) return;
    const next = Math.max(0, Math.min(rows.length - 1, i));
    setFocus(next);
    requestAnimationFrame(() => {
      const el = rowRefs.current[next];
      el?.focus({ preventScroll: true });
      el?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
    });
  }, [rows.length, reduce]);

  /** The next row after `from` that still needs a ✓ (wrapping around), skipping `except`. */
  const nextPending = useCallback((from: number, except?: string) => {
    for (let step = 1; step <= rows.length; step++) {
      const i = (from + step) % rows.length;
      if (rows[i].key !== except && stateOf(rows[i]).status !== "confirmed") return i;
    }
    return -1;
  }, [rows, stateOf]);

  const fix = useCallback((row: ReviewRow) => {
    if (editable(row)) update(row, { status: "editing", before: stateOf(row).status === "confirmed" ? "confirmed" : "pending", error: undefined });
  }, [update, stateOf]);

  const confirm = useCallback((row: ReviewRow, i: number) => {
    const state = stateOf(row);
    if (state.status === "editing") return;
    if (blocksConfirm(row, state.value)) { // nothing to say yes to yet: open the editor instead
      fix(row);
      return;
    }
    update(row, { status: "confirmed", error: undefined });
    const next = nextPending(i, row.key);
    if (next >= 0) focusRow(next);
  }, [stateOf, fix, update, nextPending, focusRow]);

  const save = useCallback((row: ReviewRow, i: number, value: string) => {
    if (row.required && !value.trim()) {
      update(row, { value, error: "This one is required." });
      return;
    }
    update(row, { value, status: "confirmed", edited: value !== row.value, error: undefined });
    const next = nextPending(i, row.key);
    focusRow(next >= 0 ? next : i);
  }, [update, nextPending, focusRow]);

  const confirmAll = useCallback(() => {
    if (!item) return;
    const waiting = rows.filter((r) => stateOf(r).status === "pending" && !needsYou(r, stateOf(r).value));
    setSheets((prev) => {
      const current = { ...(prev[item.id] || {}) };
      for (const r of waiting) current[r.key] = { ...(current[r.key] ?? fresh(r)), status: "confirmed" };
      return { ...prev, [item.id]: current };
    });
    const left = rows.filter((r) => stateOf(r).status !== "confirmed").length - waiting.length;
    if (left > 0) {
      toast({ title: `${left} ${left === 1 ? "item needs" : "items need"} you`, description: "Flagged and empty required items are never confirmed for you.", tone: "info" });
      const first = rows.findIndex((r) => stateOf(r).status !== "confirmed" && !waiting.includes(r));
      if (first >= 0) focusRow(first);
    }
  }, [item, rows, stateOf, toast, focusRow]);

  const checked = rows.filter((r) => stateOf(r).status === "confirmed").length;
  const allChecked = checked === rows.length;
  const attention = rows.filter((r) => stateOf(r).status !== "confirmed" && needsYou(r, stateOf(r).value)).length;
  const editing = rows.some((r) => stateOf(r).status === "editing");

  const goTo = useCallback((id: string | null, direction: Exit) => {
    setExit(direction);
    setCurrentId(id);
    setFocus(0);
    setZoom(false);
  }, []);

  const skip = useCallback(() => {
    if (!item) return;
    if (items.length < 2) {
      toast({ title: "This is the only one waiting", tone: "info" });
      return;
    }
    goTo(items[(index + 1) % items.length].id, "skip");
  }, [item, items, index, goTo, toast]);

  /** This one is on its way (submitted or applied by you): take it off the deck and show the next. */
  const leave = useCallback((nextId: string | null) => {
    if (!item) return;
    const others = items.filter((i) => i.id !== item.id);
    const fallback = others.length ? items[(index + 1) % items.length]?.id ?? others[0].id : null;
    setDone((prev) => new Set(prev).add(item.id));
    goTo(nextId && others.some((i) => i.id === nextId) ? nextId : fallback, "submit");
    void mutate();
    void refreshStatus();
  }, [item, items, index, goTo, mutate, refreshStatus]);

  const [clearing, setClearing] = useState(false);
  /** Empty the whole queue: every waiting application is skipped, nothing is sent. */
  const clearAll = useCallback(async () => {
    const total = data?.total ?? items.length;
    if (!total || !window.confirm(`Skip all ${total} waiting ${total === 1 ? "application" : "applications"}? Nothing is sent.`)) return;
    setClearing(true);
    try {
      const { cleared } = await post<{ cleared: number }>("/applications/review-queue/clear");
      toast({ title: `Cleared ${cleared} ${cleared === 1 ? "application" : "applications"}`, tone: "success" });
      setCurrentId(null);
    } catch (err) {
      toast({ title: "Could not clear the list", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setClearing(false);
      void mutate();
      void refreshStatus();
    }
  }, [data, items.length, toast, mutate, refreshStatus]);

  const submit = useCallback(async () => {
    if (!item || item.blocker || !allChecked || editing || busy) return;
    setBusy(true);
    try {
      const letter = rows.find((r) => r.kind === "cover_letter");
      const res = await post<DirectSubmitResponse>(`/applications/${item.id}/submit`, {
        rows: rows.filter((r) => r.kind !== "cover_letter").map((r) => ({ key: r.key, value: stateOf(r).value })),
        cover_letter: letter ? stateOf(letter).value : undefined,
      });
      toast({
        title: `Submitting to ${item.job?.company_name ?? "the company"}`,
        description: "The agent is filling the form with exactly what you checked and pressing Submit. The confirmation lands in Applications.",
        tone: "success",
      });
      leave(res.next_id);
    } catch (err) {
      toast({ title: "Could not submit", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
      if (err instanceof ApiError && err.status === 409) void mutate();
    } finally {
      setBusy(false);
    }
  }, [item, allChecked, editing, busy, rows, stateOf, toast, leave, mutate]);

  /** "Apply with the bot": the bot fills this Internshala form and submits it, no checking needed here. */
  const botReady = !!item?.bot && !item.bot.missing && !!item.blocker;
  const applyWithBot = useCallback(async () => {
    if (!item || !botReady || busy) return;
    setBusy(true);
    try {
      const res = await post<DirectSubmitResponse>(`/applications/${item.id}/bot-apply`);
      toast({
        title: `The bot is applying to ${item.job?.company_name ?? "the company"}`,
        description: "It fills the Internshala form with your resume and answers, then submits it. If a question needs you, it comes back here; the confirmation lands in Applications.",
        tone: "success",
      });
      leave(res.next_id);
    } catch (err) {
      toast({ title: "The bot couldn't start", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
      if (err instanceof ApiError && err.status === 409) void mutate();
    } finally {
      setBusy(false);
    }
  }, [item, botReady, busy, toast, leave, mutate]);

  // Y correct · N fix · J/K move · A confirm all · Enter submit · S skip · B apply with the bot (never while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!item || zoom || e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      const row = rows[focus];
      const key = e.key.toLowerCase();
      const review = !item.blocker; // a posting you apply to yourself has nothing to confirm here
      if (key === "j" || e.key === "ArrowDown") { e.preventDefault(); focusRow(focus + 1); }
      else if (key === "k" || e.key === "ArrowUp") { e.preventDefault(); focusRow(focus - 1); }
      else if (key === "y" && row && review) { e.preventDefault(); confirm(row, focus); }
      else if (key === "n" && row && review) { e.preventDefault(); fix(row); }
      else if (key === "a" && review) { e.preventDefault(); confirmAll(); }
      else if (key === "s") { e.preventDefault(); skip(); }
      else if (key === "b" && botReady && !e.repeat) { e.preventDefault(); void applyWithBot(); }
      else if (e.key === "Enter" && !e.repeat && tag !== "BUTTON" && tag !== "A" && allChecked && review) {
        e.preventDefault();
        void submit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, zoom, rows, focus, focusRow, confirm, fix, confirmAll, skip, submit, allChecked, botReady, applyWithBot]);

  const cardVariants: Variants = {
    enter: reduce ? { opacity: 0 } : { opacity: 0, y: 18 },
    center: { opacity: 1, x: 0, y: 0, transition: { duration: reduce ? 0.12 : 0.34, ease: EASE_OUT } },
    exit: (direction: Exit) => reduce
      ? { opacity: 0, transition: { duration: 0.12 } }
      : direction === "submit"
        ? { opacity: 0, y: -56, scale: 0.98, transition: { duration: 0.3, ease: [0.4, 0, 1, 1] } }
        : { opacity: 0, x: -72, transition: { duration: 0.24, ease: [0.4, 0, 1, 1] } },
  };

  const job = item?.job;
  const pct = rows.length ? Math.round((checked / rows.length) * 100) : 100;

  return (
    <div>
      <PageHeader eyebrow="Mass apply" title="Ready to submit"
        description="The agent filled these forms and stopped short of Submit. Say yes or no to each prefilled item, fix anything that's wrong right here, then send it with one click."
        actions={<>
          {count > 0 && <Button variant="ghost" onClick={() => void clearAll()} disabled={clearing}><Trash2 /> {clearing ? "Clearing…" : "Clear all"}</Button>}
          <Link href="/dashboard/review" className={buttonVariants({ variant: "ghost" })}><Layers /> Swipe Review</Link>
        </>} />

      {isLoading ? (
        <div className="mx-auto max-w-[880px] space-y-3">
          <Skeleton className="h-40" /><Skeleton className="h-20" /><Skeleton className="h-20" /><Skeleton className="h-20" />
        </div>
      ) : !item ? (
        <div className="mx-auto max-w-[880px]">
          <EmptyState icon={ListChecks} title="Nothing waiting"
            description={<>Nothing waiting. Kept jobs appear here once the agent has filled them, when something needs you.
              With “Apply automatically” on, the ones that need nothing are sent straight away: they&apos;re in Applications.
              {status?.preparing ? ` ${status.preparing} kept ${status.preparing === 1 ? "job is" : "jobs are"} being filled right now.` : ""}
              {status?.approved ? ` ${status.approved} ${status.approved === 1 ? "is" : "are"} being submitted.` : ""}
              {status?.failed ? ` ${status.failed} couldn't be prepared or sent: open them from Applications › Closed to try again.` : ""}</>}
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <Link href="/dashboard/review" className={buttonVariants()}><Layers /> Go to Swipe Review</Link>
                <Link href="/dashboard/applications" className={buttonVariants({ variant: "outline" })}><Send /> Applications</Link>
              </div>
            } />
        </div>
      ) : (
        <div className="mx-auto max-w-[880px]">
          <div className="mb-3 flex items-end justify-between gap-3">
            <p className="label-caps text-muted-foreground" aria-live="polite">
              <span className="mr-1 font-display text-2xl leading-none text-foreground tabular-nums">{index + 1}</span> of {count}
            </p>
            {attention > 0 && !item.blocker && (
              <p className="label-caps flex items-center gap-1.5 text-[10px] text-warning">
                <AlertTriangle className="h-3.5 w-3.5" /> {attention} {attention === 1 ? "item needs" : "items need"} you
              </p>
            )}
          </div>

          <AnimatePresence mode="wait" initial={false} custom={exit}>
            <motion.section key={item.id} custom={exit} variants={cardVariants} initial="enter" animate="center" exit="exit"
              aria-label={`${job?.role_title} at ${job?.company_name}`} className="border border-foreground/70 bg-background">
              <header className="flex flex-col gap-4 border-b border-line/60 p-5 sm:flex-row sm:items-start sm:justify-between sm:p-6">
                <div className="min-w-0 flex-1">
                  <p className="label-caps text-[10px] text-muted-foreground">
                    via {/internshala\.com/.test(item.apply_url || "") ? "Internshala"
                      : PLATFORM_LABELS[item.ats_platform || ""] || titleCase(item.ats_platform) || "the company site"}
                    {item.staged_at ? ` · filled ${timeAgo(item.staged_at)}` : ""}
                  </p>
                  <h2 className="display mt-3 break-words text-2xl sm:text-[1.9rem]">{job?.role_title}</h2>
                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
                    <span className="inline-flex items-center gap-1.5 font-semibold text-foreground"><Building2 className="h-4 w-4" />{job?.company_name}</span>
                    {job?.location && <span className="inline-flex min-w-0 items-center gap-1.5"><MapPin className="h-4 w-4 shrink-0" /><span className="truncate">{job.location}</span></span>}
                  </div>
                  {job && <CompanyBadge company={job.company_name} check={job.company} className="mt-3" />}
                  <div className="mt-4 flex flex-wrap gap-2">
                    {item.apply_url && (
                      <a href={item.apply_url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", size: "sm" })}>
                        Open posting <ArrowUpRight />
                      </a>
                    )}
                    <Link href={`/dashboard/applications/${item.id}`} className={buttonVariants({ variant: "ghost", size: "sm" })}>Full details</Link>
                  </div>
                </div>
                <div className="flex items-start justify-between gap-4 sm:flex-col sm:items-end">
                  <div className="flex flex-col items-start sm:items-end">
                    <span className={cn("font-display text-5xl leading-none tabular-nums", (item.match_score ?? 0) >= 70 ? "text-primary" : "text-foreground")}>
                      {item.match_score ?? "—"}
                    </span>
                    <span className="label-caps mt-1 text-[9px] text-muted-foreground">match</span>
                  </div>
                  {item.form_screenshot_url && (
                    <button type="button" onClick={() => setZoom(true)} aria-label="Enlarge the filled form screenshot"
                      className="group relative h-24 w-32 shrink-0 overflow-hidden border border-line bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={item.form_screenshot_url} alt="" className="h-full w-full object-cover object-top" />
                      <span className="absolute inset-0 flex items-center justify-center bg-foreground/0 text-background opacity-0 transition group-hover:bg-foreground/40 group-hover:opacity-100">
                        <ZoomIn className="h-5 w-5" />
                      </span>
                    </button>
                  )}
                </div>
              </header>

              {item.blocker ? (
                <div className="flex items-start gap-3 border-b border-warning/50 bg-warning/10 px-5 py-3 text-sm sm:px-6">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                  <span>
                    {item.blocker} Everything below is ready to copy over.
                    {item.bot?.missing && (
                      <> <Link href={INTEGRATIONS} className="font-semibold underline underline-offset-2 hover:text-primary">{BOT_FIX[item.bot.missing]} →</Link></>
                    )}
                  </span>
                </div>
              ) : item.needs_manual_review && item.manual_review_reason ? (
                <p className="border-b border-line/60 px-5 py-3 text-sm text-muted-foreground sm:px-6">{item.manual_review_reason}</p>
              ) : null}

              {rows.length ? (
                <ol aria-label="Prefilled items" className="divide-y divide-line/40">
                  {rows.map((row, i) => (
                    <SheetRow key={row.key} row={row} state={stateOf(row)} focused={focus === i} readOnly={!!item.blocker}
                      rowRef={(el) => { rowRefs.current[i] = el; }}
                      onFocus={() => setFocus(i)}
                      onConfirm={() => confirm(row, i)}
                      onFix={() => { setFocus(i); fix(row); }}
                      onSave={(value) => save(row, i, value)}
                      onCancel={() => { update(row, { status: stateOf(row).before ?? "pending", error: undefined }); focusRow(i); }}
                      onUndo={() => update(row, { status: "pending" })} />
                  ))}
                </ol>
              ) : (
                <p className="px-5 py-6 text-sm text-muted-foreground sm:px-6">The agent didn&apos;t find any fields to show here. Open the posting to check the form.</p>
              )}
            </motion.section>
          </AnimatePresence>

          <p className="mt-4 hidden text-center text-xs text-muted-foreground sm:block">
            {item.blocker ? <><Kbd>J</Kbd>/<Kbd>K</Kbd> move · <Kbd>S</Kbd> skip{botReady && <> · <Kbd>B</Kbd> apply with the bot</>}</> : <>
              <Kbd>Y</Kbd> correct · <Kbd>N</Kbd> fix · <Kbd>J</Kbd>/<Kbd>K</Kbd> move · <Kbd>A</Kbd> confirm all · <Kbd>Enter</Kbd> submit · <Kbd>S</Kbd> skip
            </>}
          </p>

          {/* Sticky footer: progress and the one button */}
          <div className="sticky bottom-0 z-20 -mx-4 mt-6 border-t border-line/60 bg-background/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6 lg:-mx-10 lg:px-10">
            {item.blocker ? (
              <div className="mx-auto grid max-w-[880px] grid-cols-2 gap-2 sm:flex sm:items-center sm:justify-end">
                <Button variant="ghost" size="lg" className="px-3" onClick={skip} title="Come back to this one later (S)"><SkipForward /> Skip for now</Button>
                <IAppliedButton key={item.id} applicationId={item.id} size="lg" variant="outline" onApplied={() => leave(null)} />
                {item.apply_url && (
                  <a href={item.apply_url} target="_blank" rel="noreferrer"
                    className={cn(buttonVariants({ size: "lg", variant: item.bot ? "outline" : "default" }), "col-span-2 px-4", !item.bot && "order-first sm:order-none")}>
                    Apply on {siteName(item)} <ArrowUpRight />
                  </a>
                )}
                {botReady ? (
                  <Button size="lg" className="order-first col-span-2 px-4 sm:order-none sm:px-7" onClick={() => void applyWithBot()} loading={busy}
                    title="The bot fills this Internshala form and submits it for you (B)">
                    {!busy && <Bot />} Apply with the bot
                  </Button>
                ) : item.bot?.missing ? (
                  <Link href={INTEGRATIONS} className={cn(buttonVariants({ size: "lg" }), "order-first col-span-2 px-4 sm:order-none")}
                    title="Settings › Integrations: the Internshala bot switch and how to sync your login">
                    <Bot /> {BOT_FIX[item.bot.missing]}
                  </Link>
                ) : null}
              </div>
            ) : (
              <div className="mx-auto flex max-w-[880px] flex-col gap-3 xl:flex-row xl:items-center xl:gap-6">
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="label-caps text-[10px] text-muted-foreground">Checked</span>
                    <span className="tabular-nums"><span className="font-semibold">{checked}</span> of {rows.length} checked</span>
                  </div>
                  <div className="mt-2 h-1 bg-foreground/10" role="progressbar" aria-valuemin={0} aria-valuemax={rows.length} aria-valuenow={checked}
                    aria-label="Items checked">
                    <div className={cn("h-full transition-[width] duration-300 motion-reduce:transition-none", allChecked ? "bg-success" : "bg-primary")}
                      style={{ width: `${pct}%` }} />
                  </div>
                </div>
                {/* Phones: short labels on one row (the full names stay in aria-label) */}
                <div className="grid grid-cols-[auto_auto_minmax(0,1fr)] gap-2 sm:flex sm:items-center sm:justify-end">
                  <Button variant="outline" size="lg" className="px-3 sm:px-5" onClick={confirmAll} disabled={allChecked}
                    aria-label="Confirm all" title="Confirm every item that isn't flagged (A)">
                    <CheckCheck /> <span className="sm:hidden">All</span><span className="hidden sm:inline">Confirm all</span>
                  </Button>
                  <Button variant="ghost" size="lg" className="px-3 sm:px-5" onClick={skip} aria-label="Skip for now" title="Come back to this one later (S)">
                    <SkipForward /> <span className="sm:hidden">Skip</span><span className="hidden sm:inline">Skip for now</span>
                  </Button>
                  <Button size="lg" className="px-4 sm:px-7" onClick={() => void submit()} disabled={!allChecked || editing} loading={busy}
                    aria-label="Submit application" title={allChecked ? "Submit application (Enter)" : "Check every item first"}>
                    {!busy && <Send />} <span className="sm:hidden">Submit</span><span className="hidden sm:inline">Submit application</span>
                  </Button>
                </div>
              </div>
            )}
          </div>

          <Modal open={zoom} onOpenChange={setZoom} title="Filled form" className="max-w-5xl"
            description={`${job?.role_title} at ${job?.company_name}: filled in, not submitted yet`}>
            {item.form_screenshot_url && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={item.form_screenshot_url} alt="The filled application form" className="w-full border object-contain object-top" />
            )}
          </Modal>
        </div>
      )}
    </div>
  );
}

export default function ReadyToSubmitPage() {
  return (
    <Suspense>
      <SubmitInner />
    </Suspense>
  );
}
