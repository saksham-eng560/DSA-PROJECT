"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, cloneElement, isValidElement, useEffect, useId, useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Bot, CheckCircle2, Copy, Cpu, Download, KeyRound, Link2, Mail, Puzzle, RefreshCw, Save, Trash2, Zap } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { TagInput } from "@/components/tag-input";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Modal } from "@/components/modal";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Select } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { useIntegrations, useMe } from "@/hooks/use-applications";
import { ApiError, api, del, fetcher, patch, post, put } from "@/lib/api-client";
import type { FieldMapping, Integrations, LLMTestResult, LocationFocus, OllamaPullProgress, Preferences, StandardField, StudentInfo } from "@/lib/types";
import { PLATFORM_LABELS, cn, timeAgo } from "@/lib/utils";

const ALL_PLATFORMS = ["internshala", "naukri", "unstop", "remote_boards", "internships", "greenhouse", "lever", "ashby", "workday", "linkedin", "indeed", "glassdoor", "wellfound", "generic"];
const pill = (on: boolean) => cn("rounded-full border px-3 py-1 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
  on ? "border-primary bg-primary text-primary-foreground" : "border-foreground/30 hover:border-foreground");

const PRESETS = [
  { name: "ai-python", title: "AI + Python · 2nd year", text: "AI engineer, GenAI and LLM internships first, then Python / FastAPI backend ones. At most 2 months, open to 2nd-year students, at startups and good companies, in Delhi NCR first, then the rest of India and remote. Nothing from the US or abroad. Java, PHP, Node.js, frontend, mobile, data and non-tech roles are left out. Applying it replaces your search filters with exactly these." },
] as const;

function MassApplyPanel() {
  const { data: me, mutate } = useMe();
  const toast = useToast();
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [applying, setApplying] = useState<string | null>(null);
  const { saving, run } = useSaver();
  useEffect(() => { if (me) setPrefs(me.preferences); }, [me]);
  if (!prefs) return null;
  const set = <K extends keyof Preferences>(k: K, v: Preferences[K]) => setPrefs({ ...prefs, [k]: v });
  const save = () => run(async () => {
    await put("/users/me/preferences", { preferences: {
      review_mode: prefs.review_mode, resume_strategy: prefs.resume_strategy, auto_submit_kept: prefs.auto_submit_kept, trust_generated_answers: prefs.trust_generated_answers,
      auto_keep_min_score: prefs.auto_keep_min_score, max_jobs_per_source: prefs.max_jobs_per_source,
      exclude_no_sponsorship: prefs.exclude_no_sponsorship, max_applications_per_day: prefs.max_applications_per_day,
      internshala_share: prefs.internshala_share ?? 25, internshala_per_scan: prefs.internshala_per_scan ?? 10,
      source_share: prefs.source_share ?? 40, fill_deck_to: prefs.fill_deck_to ?? 40,
      skip_suspicious_companies: prefs.skip_suspicious_companies ?? true,
      verified_companies_only: prefs.verified_companies_only ?? false,
      scan_top_companies: prefs.scan_top_companies ?? true,
      sources: { internship_lists: prefs.sources.internship_lists || [] },
    } });
    await mutate();
  });
  const applyPreset = async (name: string) => {
    setApplying(name);
    try {
      await post(`/users/me/preferences/preset/${name}`);
      await mutate();
      toast({ title: "Preset applied", tone: "success", description: "Your search is set to AI and Python / FastAPI internships of at most 2 months for a 2nd-year student, at good companies in Delhi NCR and India. Cards that don't fit are skipped; run a scan to fill your deck." });
    } catch (err) {
      toast({ title: "Could not apply preset", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setApplying(null);
    }
  };
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Your preset</CardTitle>
          <CardDescription>One click sets every search filter to this, and removes filters left over from earlier settings. Your account, resume, saved answers and notifications stay.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-px border-t bg-border p-0">
          {PRESETS.map((p) => (
            <div key={p.name} className="flex flex-col bg-card p-5">
              <p className="display text-xl">{p.title}</p>
              <p className="mt-2 flex-1 text-sm text-muted-foreground">{p.text}</p>
              <Button className="mt-4 self-start" size="sm" loading={applying === p.name} onClick={() => applyPreset(p.name)}>Apply preset</Button>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>How jobs are picked</CardTitle>
          <CardDescription>Swipe mode never skips a job for a low score — you decide in Swipe Review.</CardDescription>
        </CardHeader>
        <CardContent>
          <Row label="Review mode" hint="Auto mode skips jobs under your match threshold and prepares the rest for approval.">
            <div className="flex flex-wrap gap-2">
              {(["swipe", "auto"] as const).map((m) => (
                <button key={m} type="button" className={pill(prefs.review_mode === m)} aria-pressed={prefs.review_mode === m} onClick={() => set("review_mode", m)}>
                  {m === "swipe" ? "Swipe Review (recommended)" : "Automatic threshold"}
                </button>
              ))}
            </div>
          </Row>
          <Row label="Resume to send" hint="Your original file keeps your design and every word. Light tweaks keeps every word too and only moves the most relevant bullets, projects and skills to the top for each job (re-rendered in the app's clean template). Full AI tailoring rewrites wording, guarded against invented facts.">
            <div className="flex flex-wrap gap-2">
              {([["original", "Your original file"], ["light", "Light tweaks"], ["full", "Full AI tailoring"]] as const).map(([value, label]) => (
                <button key={value} type="button" className={pill((prefs.resume_strategy || "original") === value)} onClick={() => set("resume_strategy", value)}>
                  {label}
                </button>
              ))}
            </div>
          </Row>
          <Row label="Apply automatically after I keep" hint="Kept jobs are submitted as soon as the form is filled. Blank eligibility questions always wait for you.">
            <Switch checked={prefs.auto_submit_kept} onCheckedChange={(v) => set("auto_submit_kept", v)} label="Apply automatically after keeping" />
          </Row>
          <Row label="Trust AI answers to open questions" hint="“Why this company?”-style answers written by the agent won't hold a kept job back. Visa, work authorization and background questions are never guessed.">
            <Switch checked={prefs.trust_generated_answers} onCheckedChange={(v) => set("trust_generated_answers", v)} label="Trust generated answers" />
          </Row>
          <Row label="Keep automatically at score" hint="Optional: jobs scoring at least this are kept without a swipe. Leave empty to swipe everything yourself.">
            <Input type="number" min={0} max={100} className="max-w-[8rem]" placeholder="off" value={prefs.auto_keep_min_score ?? ""}
              onChange={(e) => set("auto_keep_min_score", e.target.value === "" ? null : Number(e.target.value))} />
          </Row>
          <Row label="Search top companies in every scan" hint="Big tech, product companies, renowned Indian and global startups and AI companies, from their own job boards. They get their own page: Top companies.">
            <Switch checked={prefs.scan_top_companies ?? true} onCheckedChange={(v) => set("scan_top_companies", v)} label="Search top companies in every scan" />
          </Row>
          <Row label="Internshala postings per scan" hint="A scan adds at most this many new Internshala postings, the best ones (known companies, no warning signs). Ones you already have don't count.">
            <Input type="number" min={0} max={50} className="max-w-[8rem]" value={prefs.internshala_per_scan ?? 10}
              onChange={(e) => set("internshala_per_scan", Math.max(0, Math.min(50, Number(e.target.value) || 0)))} aria-label="Internshala postings per scan" />
          </Row>
          <Row label="Internshala share of each scan" hint="And never more than this % of a scan's new postings. 25 % = one Internshala posting for every three from elsewhere.">
            <div className="flex items-center gap-2">
              <Input type="number" min={0} max={100} className="max-w-[8rem]" value={prefs.internshala_share ?? 25}
                onChange={(e) => set("internshala_share", Math.max(0, Math.min(100, Number(e.target.value) || 0)))} aria-label="Internshala share in percent" />
              <span className="text-sm text-muted-foreground">%</span>
            </div>
          </Row>
          <Row label="Most from one job site" hint="No single site (Internshala, LinkedIn, Naukri...) fills more than this % of a scan's new postings while the others have postings to offer. A thin scan is never cut just to keep the mix.">
            <div className="flex items-center gap-2">
              <Input type="number" min={0} max={100} className="max-w-[8rem]" value={prefs.source_share ?? 40}
                onChange={(e) => set("source_share", Math.max(0, Math.min(100, Number(e.target.value) || 0)))} aria-label="Most from one job site in percent" />
              <span className="text-sm text-muted-foreground">%</span>
            </div>
          </Row>
          <Row label="Keep Swipe Review filled to" hint="When fewer cards than this wait for you, the closest near misses are shown too: longer internships, roles just outside your tech focus, small unverified companies. Each says which setting it misses. Companies you avoid, excluded titles, stacks you skip, scams and roles you aren't eligible for never are. 0 = only exact matches.">
            <Input type="number" min={0} max={200} className="max-w-[8rem]" value={prefs.fill_deck_to ?? 40}
              onChange={(e) => set("fill_deck_to", Math.max(0, Math.min(200, Number(e.target.value) || 0)))} aria-label="Keep Swipe Review filled to" />
          </Row>
          <Row label="Skip possible fraud" hint="The company check skips postings with scam signs (asks for a fee or deposit, WhatsApp-only contact, earn-per-day promises, MLM). Either way, only verified companies are ever applied to automatically.">
            <Switch checked={prefs.skip_suspicious_companies ?? true} onCheckedChange={(v) => set("skip_suspicious_companies", v)} label="Skip possible fraud" />
          </Row>
          <Row label="Good companies only" hint="Show only internships at companies the company check verified: big tech, product companies, renowned Indian and global startups, AI companies, and startups posting on their own job board (Greenhouse, Lever, Ashby...). Companies you mark legit are always in. Small unknown companies are left out (or shown as near misses when Swipe Review is thin).">
            <Switch checked={prefs.verified_companies_only ?? false} onCheckedChange={(v) => set("verified_companies_only", v)} label="Good companies only" />
          </Row>
          <Row label="Skip jobs without visa sponsorship" hint="For international students: drop postings that say they don't sponsor or require citizenship.">
            <Switch checked={prefs.exclude_no_sponsorship} onCheckedChange={(v) => set("exclude_no_sponsorship", v)} label="Skip jobs without sponsorship" />
          </Row>
          <Row label="Max applications per day" hint="Per-platform caps (e.g. 40 Greenhouse, 10 Workday) still apply to stay under the radar.">
            <Input type="number" min={1} max={200} className="max-w-[8rem]" value={prefs.max_applications_per_day} onChange={(e) => set("max_applications_per_day", Number(e.target.value))} />
          </Row>
          <Row label="Jobs per source per scan" hint="How many postings each source may return per scan (10–1000).">
            <Input type="number" min={10} max={1000} className="max-w-[8rem]" placeholder="50" value={prefs.max_jobs_per_source ?? ""}
              onChange={(e) => set("max_jobs_per_source", e.target.value === "" ? null : Number(e.target.value))} />
          </Row>
          <Row label="Curated internship lists" hint="simplify-internships, vanshb03-internships, simplify-new-grad — or any listings.json URL in the same format.">
            <TagInput value={prefs.sources.internship_lists || []} onChange={(v) => setPrefs({ ...prefs, sources: { ...prefs.sources, internship_lists: v } })} placeholder="simplify-internships" />
          </Row>
          <div className="pt-4"><Button onClick={save} loading={saving}><Save /> Save mass-apply settings</Button></div>
        </CardContent>
      </Card>
    </div>
  );
}

// Controls a Row can label directly (htmlFor/id); anything else is wrapped in a labelled group.
const LABELLABLE: unknown[] = [Input, Select, Switch, TagInput];

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  const id = useId();
  const single = isValidElement<{ id?: string }>(children) && LABELLABLE.includes(children.type) && !children.props.id;
  return (
    <div className="grid grid-cols-1 gap-2 border-b border-border/70 py-5 last:border-0 md:grid-cols-[18rem_minmax(0,1fr)] md:gap-8">
      <div>
        <Label id={`${id}-label`} htmlFor={single ? id : undefined} className="text-sm font-semibold">{label}</Label>
        {hint && <p className="mt-1 break-words text-xs text-muted-foreground">{hint}</p>}
      </div>
      {single ? <div>{cloneElement(children, { id })}</div> : <div role="group" aria-labelledby={`${id}-label`}>{children}</div>}
    </div>
  );
}

function useSaver() {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const run = async (fn: () => Promise<unknown>, ok = "Saved") => {
    setSaving(true);
    try {
      await fn();
      toast({ title: ok, tone: "success" });
      return true;
    } catch (err) {
      toast({ title: "Could not save", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
      return false;
    } finally {
      setSaving(false);
    }
  };
  return { saving, run };
}

const YEAR_NAMES = ["1st", "2nd", "3rd", "4th", "5th"];
const SOURCE_NOTE = { you: "", resume: " (from your resume)", estimate: " (estimated)", "": "" } as const;

/** Year of study and graduating batch: internships only for other students are skipped. */
function StudentRows({ prefs, set }: {
  prefs: Preferences; set: <K extends keyof Preferences>(k: K, v: Preferences[K]) => void;
}) {
  const { data: info } = useSWR<StudentInfo>("/users/me/student", fetcher);
  const guess = info && info.graduation_year_source !== "you" ? info.graduation_year : null;
  return (
    <>
      <Row label="Year of study" hint="Internships only for other students are skipped: final-year only, rising seniors, PhD / Master's / MBA only, or another graduating batch. Ones that name your year (like Google STEP for 1st and 2nd years) are tagged.">
        <Select value={prefs.year_of_study == null ? "" : String(prefs.year_of_study)} className="max-w-xs" aria-label="Year of study"
          onChange={(e) => set("year_of_study", e.target.value ? Number(e.target.value) : null)}>
          <option value="">Any year (don&apos;t check)</option>
          {YEAR_NAMES.map((name, i) => <option key={name} value={i + 1}>{name} year</option>)}
        </Select>
      </Row>
      <Row label="Internship length" hint="Internships whose posting says they're longer are skipped (Internshala's duration, “6-month internship”, “for 12 weeks”). Postings that don't say are kept.">
        <Select value={prefs.max_internship_months == null ? "" : String(prefs.max_internship_months)} className="max-w-xs" aria-label="Longest internship"
          onChange={(e) => set("max_internship_months", e.target.value ? Number(e.target.value) : null)}>
          <option value="">Any length</option>
          {[1, 2, 3, 4, 6].map((m) => <option key={m} value={m}>At most {m} month{m === 1 ? "" : "s"}</option>)}
        </Select>
      </Row>
      <Row label="Graduating in" hint="Postings for another batch (“2026 graduates only”) are skipped. Leave empty to use your resume's education.">
        <div className="flex max-w-md items-center gap-3">
          <Input type="number" min={2000} max={2100} className="max-w-[8rem]" aria-label="Graduation year"
            value={prefs.graduation_year ?? ""} placeholder={guess ? String(guess) : "e.g. 2029"}
            onChange={(e) => set("graduation_year", e.target.value ? Number(e.target.value) : null)} />
          {guess != null && prefs.graduation_year == null && (
            <span className="text-sm text-muted-foreground">{guess}{SOURCE_NOTE[info!.graduation_year_source]}</span>
          )}
        </div>
      </Row>
    </>
  );
}

function PreferencesForm({ sourcesOnly = false }: { sourcesOnly?: boolean }) {
  const { data: me, mutate } = useMe();
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const { saving, run } = useSaver();
  useEffect(() => { if (me) setPrefs(me.preferences); }, [me]);
  if (!prefs) return null;
  const set = <K extends keyof Preferences>(k: K, v: Preferences[K]) => setPrefs({ ...prefs, [k]: v });
  const setSource = (k: keyof Preferences["sources"], v: string[]) => setPrefs({ ...prefs, sources: { ...prefs.sources, [k]: v } });
  const save = () => run(async () => { await put("/users/me/preferences", { preferences: prefs }); await mutate(); });

  if (sourcesOnly) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Job sources</CardTitle>
          <CardDescription>Where the agent looks. ATS boards use public APIs (fast, no login). Job boards search by your target roles & locations.</CardDescription>
        </CardHeader>
        <CardContent>
          <Row label="Platforms to scan" hint="Indeed, Glassdoor and Wellfound use a real browser and may need residential proxies to avoid bot blocks.">
            <div className="flex flex-wrap gap-2">
              {ALL_PLATFORMS.map((p) => (
                <button key={p} type="button" onClick={() => set("platforms", prefs.platforms.includes(p) ? prefs.platforms.filter((x) => x !== p) : [...prefs.platforms, p])}
                  className={pill(prefs.platforms.includes(p))} aria-pressed={prefs.platforms.includes(p)}>
                  {PLATFORM_LABELS[p] || p}
                </button>
              ))}
            </div>
          </Row>
          <Row label="Greenhouse boards" hint="Board tokens, e.g. “stripe” from job-boards.greenhouse.io/stripe">
            <TagInput value={prefs.sources.greenhouse_boards} onChange={(v) => setSource("greenhouse_boards", v)} placeholder="stripe, airbnb…" />
          </Row>
          <Row label="Lever companies" hint="Slug from jobs.lever.co/<company>">
            <TagInput value={prefs.sources.lever_companies} onChange={(v) => setSource("lever_companies", v)} placeholder="netflix…" />
          </Row>
          <Row label="Ashby boards" hint="Slug from jobs.ashbyhq.com/<board>">
            <TagInput value={prefs.sources.ashby_boards} onChange={(v) => setSource("ashby_boards", v)} placeholder="openai…" />
          </Row>
          <Row label="Workday career sites" hint="Full site URL, e.g. https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite">
            <TagInput value={prefs.sources.workday_sites} onChange={(v) => setSource("workday_sites", v)} placeholder="https://…myworkdayjobs.com/…" />
          </Row>
          <Row label="Company careers pages" hint="Any careers page — the agent reads JSON-LD job postings and follows embedded ATS boards.">
            <TagInput value={prefs.sources.career_pages} onChange={(v) => setSource("career_pages", v)} placeholder="https://company.com/careers" />
          </Row>
          <Row label="Internshala searches" hint="Optional: paste any Internshala search URL (filters and all). Without these the agent searches your roles in your prime cities, work-from-home and all of India. Apply on Internshala yourself and click “I Applied”, or turn on the Internshala bot in Integrations.">
            <TagInput value={prefs.sources.internshala_urls || []} onChange={(v) => setSource("internshala_urls", v)} placeholder="https://internshala.com/internships/python-django-internship-in-delhi/" />
          </Row>
          <div className="pt-4"><Button onClick={save} loading={saving}><Save /> Save sources</Button></div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
    <FocusCard prefs={prefs} setPrefs={setPrefs} onSave={save} saving={saving} />
    <Card>
      <CardHeader>
        <CardTitle>Search preferences</CardTitle>
        <CardDescription>What the agent looks for and how aggressively it applies.</CardDescription>
      </CardHeader>
      <CardContent>
        <Row label="Target roles" hint="Job titles to look for">
          <TagInput value={prefs.target_roles} onChange={(v) => set("target_roles", v)} placeholder="Software Engineer, Backend Engineer…" />
        </Row>
        <Row label="Tech focus" hint="A posting must name at least one of these (judged on the whole posting). Leave empty to keep every stack.">
          <TagInput value={prefs.focus_skills || []} onChange={(v) => set("focus_skills", v)} placeholder="AI, LLMs, Python, FastAPI…" />
        </Row>
        <Row label="Skip these technologies" hint="Roles in these are left out: a title that names one, or a posting that asks for one and none of your languages or frameworks.">
          <TagInput value={prefs.avoid_skills || []} onChange={(v) => set("avoid_skills", v)} placeholder="Java, Spring Boot…" />
        </Row>
        <Row label="Target locations">
          <TagInput value={prefs.target_locations} onChange={(v) => set("target_locations", v)} placeholder="San Francisco, New York…" />
        </Row>
        <Row label="Work arrangement">
          <Select value={prefs.remote_preference} onChange={(e) => set("remote_preference", e.target.value as Preferences["remote_preference"])} className="max-w-xs">
            <option value="any">Any</option><option value="remote">Remote only</option><option value="hybrid">Hybrid / remote OK</option><option value="onsite">On-site</option>
          </Select>
        </Row>
        <Row label="Internships only" hint="Intern roles at every company: big tech, top companies and the rest. Full-time, new-grad and contract jobs are never shown or applied to.">
          <Switch checked={prefs.internships_only ?? true} onCheckedChange={(v) => set("internships_only", v)} label="Internships only" />
        </Row>
        {(prefs.internships_only ?? true) ? (
          <StudentRows prefs={prefs} set={set} />
        ) : (
          <Row label="Job types">
            <div className="flex flex-wrap gap-2">
              {["full-time", "part-time", "internship", "contract", "freelance"].map((t) => (
                <button key={t} type="button" onClick={() => set("job_types", prefs.job_types.includes(t) ? prefs.job_types.filter((x) => x !== t) : [...prefs.job_types, t])}
                  className={cn(pill(prefs.job_types.includes(t)), "capitalize")} aria-pressed={prefs.job_types.includes(t)}>{t}</button>
              ))}
            </div>
          </Row>
        )}
        <Row label="Salary range" hint="Salary questions are answered with the bottom of this range.">
          <div className="flex max-w-md items-center gap-2">
            <Input type="number" placeholder="Min" aria-label="Minimum salary" value={prefs.salary_min ?? ""} onChange={(e) => set("salary_min", e.target.value ? Number(e.target.value) : null)} />
            <span className="text-muted-foreground">–</span>
            <Input type="number" placeholder="Max" aria-label="Maximum salary" value={prefs.salary_max ?? ""} onChange={(e) => set("salary_max", e.target.value ? Number(e.target.value) : null)} />
            <Input className="w-24" aria-label="Salary currency" value={prefs.salary_currency} onChange={(e) => set("salary_currency", e.target.value.toUpperCase())} />
          </div>
        </Row>
        <Row label="Companies to avoid">
          <TagInput value={prefs.companies_to_avoid} onChange={(v) => set("companies_to_avoid", v)} />
        </Row>
        <Row label="Companies to target">
          <TagInput value={prefs.companies_to_target} onChange={(v) => set("companies_to_target", v)} />
        </Row>
        <Row label="Exclude titles containing">
          <TagInput value={prefs.keywords_exclude} onChange={(v) => set("keywords_exclude", v)} placeholder="Principal, Manager…" />
        </Row>
        <Row label="Match threshold" hint="Automatic mode only: jobs scoring at or above this are prepared for you. Swipe mode never skips on score.">
          <div className="flex max-w-md items-center gap-4">
            <input type="range" min={0} max={100} aria-label="Match threshold" value={prefs.auto_apply_threshold} onChange={(e) => set("auto_apply_threshold", Number(e.target.value))} className="flex-1 accent-[hsl(var(--primary))]" />
            <span className="w-10 text-right font-medium tabular-nums">{prefs.auto_apply_threshold}</span>
          </div>
        </Row>
        <Row label="Max applications per day">
          <Input type="number" min={1} max={200} className="max-w-[8rem]" value={prefs.max_applications_per_day} onChange={(e) => set("max_applications_per_day", Number(e.target.value))} />
        </Row>
        <Row label="Posted within (days)">
          <Input type="number" min={1} max={90} className="max-w-[8rem]" value={prefs.posted_within_days} onChange={(e) => set("posted_within_days", Number(e.target.value))} />
        </Row>
        <Row label="Automatic scans" hint="Celery Beat checks hourly and scans when your interval has elapsed.">
          <div className="flex items-center gap-3">
            <Switch checked={prefs.scan_enabled} onCheckedChange={(v) => set("scan_enabled", v)} label="Automatic scans" />
            <span className="text-sm text-muted-foreground">every</span>
            <Input type="number" min={1} max={168} className="w-20" aria-label="Scan interval in hours" value={prefs.scan_interval_hours} onChange={(e) => set("scan_interval_hours", Number(e.target.value))} />
            <span className="text-sm text-muted-foreground">hours</span>
          </div>
        </Row>
        <Row label="Cover letters">
          <Switch checked={prefs.cover_letter_enabled} onCheckedChange={(v) => set("cover_letter_enabled", v)} label="Generate cover letters" />
        </Row>
        <Row label="Resume PDF template">
          <Select className="max-w-xs" value={prefs.resume_template || "classic"} onChange={(e) => set("resume_template", e.target.value)}>
            <option value="classic">Classic</option><option value="modern">Modern</option>
          </Select>
        </Row>
        <Row label="Timezone" hint="Used to interpret interview times in e-mails.">
          <Input className="max-w-xs" value={prefs.timezone} onChange={(e) => set("timezone", e.target.value)} placeholder={Intl.DateTimeFormat().resolvedOptions().timeZone} />
        </Row>
        <div className="pt-4"><Button onClick={save} loading={saving}><Save /> Save preferences</Button></div>
      </CardContent>
    </Card>
    </div>
  );
}

const DEFAULT_FOCUS: LocationFocus = { enabled: true, country: "India", prime_cities: ["Delhi", "New Delhi", "Delhi NCR", "Gurugram", "Noida"], country_share: 90 };

/** Internships for one season, mostly in one country, with prime cities first. */
function FocusCard({ prefs, setPrefs, onSave, saving }: {
  prefs: Preferences; setPrefs: (p: Preferences) => void; onSave: () => void; saving: boolean;
}) {
  const focus = { ...DEFAULT_FOCUS, ...(prefs.location_focus || {}) };
  const setFocus = (patch: Partial<LocationFocus>) => setPrefs({ ...prefs, location_focus: { ...focus, ...patch } });
  const internshipsOnly = prefs.job_types.length === 1 && prefs.job_types[0] === "internship";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Internship focus</CardTitle>
        <CardDescription>Which internships the agent hunts for, and where. Swipe Review shows your prime cities first.</CardDescription>
      </CardHeader>
      <CardContent>
        <Row label="Internships only" hint="Scan internships and nothing else for now. Turn off to add full-time roles back.">
          <Switch checked={internshipsOnly} label="Internships only"
            onCheckedChange={(v) => setPrefs({ ...prefs, job_types: v ? ["internship"] : ["internship", "full-time"], experience_level: v ? ["internship"] : prefs.experience_level })} />
        </Row>
        <Row label="Season" hint="Postings clearly for another term are skipped; ones that name this season come first. Leave empty for any season.">
          <Input className="max-w-xs" value={prefs.internship_season || ""} placeholder="Summer 2027"
            onChange={(e) => setPrefs({ ...prefs, internship_season: e.target.value || null })} />
        </Row>
        <Row label="Focus on one country" hint="Keeps most of every scan in this country; the rest can come from anywhere (remote roles first).">
          <div className="flex flex-wrap items-center gap-3">
            <Switch checked={focus.enabled !== false} onCheckedChange={(v) => setFocus({ enabled: v })} label="Focus on one country" />
            <Input className="max-w-[12rem]" aria-label="Country" value={focus.country} onChange={(e) => setFocus({ country: e.target.value })} disabled={focus.enabled === false} />
          </div>
        </Row>
        <Row label={`Share in ${focus.country || "the country"}`} hint="About this much of each scan's new internships is in the country.">
          <div className="flex max-w-md items-center gap-4">
            <Slider value={[focus.country_share]} min={50} max={100} step={5} disabled={focus.enabled === false}
              onValueChange={(v) => setFocus({ country_share: v[0] })} aria-label="Share of internships in the country" />
            <span className="w-12 text-right font-medium tabular-nums">{focus.country_share}%</span>
          </div>
        </Row>
        <Row label="Prime cities" hint="Shown first in Swipe Review and searched first on Internshala, LinkedIn and Indeed.">
          <TagInput value={focus.prime_cities} onChange={(v) => setFocus({ prime_cities: v })} placeholder="Delhi, Gurugram, Noida…" />
        </Row>
        <div className="pt-4"><Button onClick={onSave} loading={saving}><Save /> Save internship focus</Button></div>
      </CardContent>
    </Card>
  );
}

function ProfileForm() {
  const { data: me, mutate } = useMe();
  const [form, setForm] = useState({ full_name: "", phone: "", location: "", linkedin_url: "" });
  const { saving, run } = useSaver();
  useEffect(() => {
    if (me) setForm({ full_name: me.full_name, phone: me.phone || "", location: me.location || "", linkedin_url: me.linkedin_url || "" });
  }, [me]);
  return (
    <Card>
      <CardHeader><CardTitle>Profile</CardTitle><CardDescription>{me?.email}</CardDescription></CardHeader>
      <CardContent>
        <div className="grid max-w-2xl gap-4 sm:grid-cols-2">
          {([["full_name", "Full name"], ["phone", "Phone"], ["location", "Location"], ["linkedin_url", "LinkedIn URL"]] as const).map(([k, label]) => (
            <div key={k} className="space-y-1.5"><Label htmlFor={`profile-${k}`}>{label}</Label><Input id={`profile-${k}`} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></div>
          ))}
        </div>
        <Button className="mt-4" loading={saving} onClick={() => run(async () => { await patch("/users/me", form); await mutate(); })}><Save /> Save profile</Button>
      </CardContent>
    </Card>
  );
}

function FieldMappingsForm() {
  const { data, mutate } = useSWR<{ mappings: FieldMapping[]; standard_fields: Record<string, StandardField> }>("/users/me/field-mappings", fetcher);
  const [values, setValues] = useState<Record<string, string>>({});
  const [custom, setCustom] = useState({ name: "", value: "" });
  const { saving, run } = useSaver();
  useEffect(() => {
    if (data) setValues(Object.fromEntries(data.mappings.map((m) => [m.field_name, m.field_value])));
  }, [data]);
  if (!data) return null;
  const standardKeys = Object.keys(data.standard_fields);
  const customMappings = data.mappings.filter((m) => !standardKeys.includes(m.field_name));
  const save = () => run(async () => {
    const mappings = Object.entries(values).filter(([, v]) => v !== "" && v !== "********").map(([field_name, field_value]) => ({ field_name, field_value }));
    await put("/users/me/field-mappings", { mappings });
    await mutate();
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Saved answers</CardTitle>
        <CardDescription>Exact answers for common application questions. These always win over AI-generated answers. Passwords are stored encrypted.</CardDescription>
      </CardHeader>
      <CardContent>
        {Object.entries(data.standard_fields).map(([key, field]) => (
          <Row key={key} label={field.label}>
            {field.options ? (
              <Select className="max-w-sm" value={values[key] || ""} onChange={(e) => setValues({ ...values, [key]: e.target.value })}>
                <option value="">— not set —</option>
                {field.options.map((o) => <option key={o} value={o}>{o}</option>)}
              </Select>
            ) : (
              <Input className="max-w-sm" type={field.type === "password" ? "password" : field.type === "number" ? "number" : "text"}
                value={values[key] || ""} onChange={(e) => setValues({ ...values, [key]: e.target.value })}
                placeholder={field.type === "password" ? "Used when an ATS requires an account" : ""} />
            )}
          </Row>
        ))}
        {customMappings.map((m) => (
          <Row key={m.field_name} label={m.field_name}>
            <div className="flex max-w-sm gap-2">
              <Input aria-label={m.field_name} value={values[m.field_name] || ""} onChange={(e) => setValues({ ...values, [m.field_name]: e.target.value })} />
              <Button variant="ghost" size="icon" aria-label={`Delete ${m.field_name}`} onClick={() => run(async () => { await del(`/users/me/field-mappings/${m.field_name}`); await mutate(); }, "Deleted")}><Trash2 /></Button>
            </div>
          </Row>
        ))}
        <Row label="Add a custom answer" hint="Use the question wording, e.g. “Do you have a driver's license”">
          <div className="flex max-w-xl gap-2">
            <Input placeholder="Question / field name" aria-label="Question or field name" value={custom.name} onChange={(e) => setCustom({ ...custom, name: e.target.value })} />
            <Input placeholder="Answer" aria-label="Answer" value={custom.value} onChange={(e) => setCustom({ ...custom, value: e.target.value })} />
            <Button variant="outline" disabled={!custom.name || !custom.value} onClick={() => { setValues({ ...values, [custom.name.toLowerCase().replace(/\s+/g, "_")]: custom.value }); setCustom({ name: "", value: "" }); }}>Add</Button>
          </div>
        </Row>
        <div className="pt-4"><Button onClick={save} loading={saving}><Save /> Save answers</Button></div>
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------------ AI model
const PROVIDER_LABELS: Record<string, string> = { anthropic: "Claude (Anthropic)", openai: "OpenAI", ollama: "Ollama" };
const providerLabel = (name: string | null) => (name ? PROVIDER_LABELS[name] ?? name : "None");

function formatBytes(bytes: number) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  return `${Math.round(bytes / 1e6)} MB`;
}

const SETUP_SCRIPT = "curl -fsSL https://raw.githubusercontent.com/saksham-eng560/autoapply-ai/main/scripts/server-setup.sh | WITH_OLLAMA=1 bash";

const OLLAMA_SETUPS: { id: string; title: string; steps: React.ReactNode[]; env: string }[] = [
  {
    id: "mac",
    title: "This computer (Mac)",
    steps: [
      <>Install the Ollama app from <a className="text-primary underline-offset-4 hover:underline" href="https://ollama.com/download" target="_blank" rel="noreferrer">ollama.com/download</a> and open it once.</>,
      <>Add these lines to <code>.env</code> in the AutoApply folder.</>,
      <>Restart with <code>./start.sh --ollama</code>. It checks Ollama and downloads the model (about 3.4 GB) for you.</>,
    ],
    env: "LLM_PROVIDER=ollama\nOLLAMA_MODEL=qwen3.5:4b\nOLLAMA_BASE_URL=http://localhost:11434",
  },
  {
    id: "server",
    title: "Your server (Docker)",
    steps: [
      <>Easiest: re-run the setup script with Ollama on. It sets all of this and downloads the model: <code className="break-all">{SETUP_SCRIPT}</code></>,
      <>Or add these lines to <code>~/autoapply-ai/.env</code> yourself and restart with <code>docker compose -f docker-compose.prod.yml up -d</code>.</>,
      <>Then press <strong>Download model</strong> here. Ollama runs in its own container and is never exposed to the internet.</>,
    ],
    env: "COMPOSE_PROFILES=ollama\nLLM_PROVIDER=ollama\nOLLAMA_BASE_URL=http://ollama:11434\nOLLAMA_MODEL=qwen3.5:4b",
  },
  {
    id: "cloud",
    title: "Ollama Cloud",
    steps: [
      <>Create an API key at <a className="text-primary underline-offset-4 hover:underline" href="https://ollama.com/settings/keys" target="_blank" rel="noreferrer">ollama.com/settings/keys</a>.</>,
      <>Add these lines to <code>.env</code>, then paste the key after <code>OLLAMA_API_KEY=</code> in the file itself.</>,
      <>Restart the app. There is nothing to download. The free plan answers one request at a time.</>,
    ],
    env: "LLM_PROVIDER=ollama\nOLLAMA_BASE_URL=https://ollama.com\nOLLAMA_MODEL=gpt-oss:120b\n# your key goes after the = (in .env only)\nOLLAMA_API_KEY=",
  },
];

function CopyBlock({ text, label }: { text: string; label: string }) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast({ title: "Copied", tone: "success" });
    } catch {
      toast({ title: "Copy blocked by the browser", description: "Select the text and copy it instead.", tone: "error" });
    }
  };
  return (
    <div className="relative">
      <pre className="overflow-x-auto border border-border bg-muted p-3 pr-12 font-mono text-xs leading-relaxed">{text}</pre>
      <Button size="icon" variant="outline" className="absolute right-2 top-2 h-7 w-7" aria-label={`Copy ${label}`} onClick={copy}><Copy /></Button>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="label-caps text-muted-foreground">{label}</p>
      <p className="mt-1 break-words font-medium">{children}</p>
    </div>
  );
}

function AIModelCard({ integ, refresh }: { integ: Integrations; refresh: () => Promise<unknown> }) {
  const toast = useToast();
  const { llm } = integ;
  const ollama = llm.ollama;
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<LLMTestResult | null>(null);
  const [starting, setStarting] = useState(false);
  const [pull, setPull] = useState<OllamaPullProgress | null>(ollama.pull);
  const [setup, setSetup] = useState(ollama.cloud ? "cloud" : "mac");
  const pulling = pull?.status === "pulling";

  useEffect(() => {  // a download started earlier (another tab, before a reload) keeps showing
    if (ollama.pull?.status === "pulling") setPull(ollama.pull);
  }, [ollama.pull]);

  useEffect(() => {
    if (!pulling) return;
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const next = await api<OllamaPullProgress>("/users/me/integrations/ollama/pull");
        if (stopped) return;
        setPull(next);
        if (next.status === "success") {
          toast({ title: `Downloaded ${next.model}`, tone: "success" });
          void refresh();
        } else if (next.status === "error") {
          toast({ title: "Download failed", description: next.error ?? undefined, tone: "error" });
        }
      } catch {
        /* a missed poll is fine: the next one catches up */
      }
    }, 1500);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [pulling, refresh, toast]);

  const startPull = async () => {
    setStarting(true);
    try {
      setPull(await post<OllamaPullProgress>("/users/me/integrations/ollama/pull"));
    } catch (err) {
      toast({ title: "Could not start the download", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setStarting(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      setResult(await post<LLMTestResult>("/users/me/integrations/llm/test"));
    } catch (err) {
      setResult({ ok: false, provider: llm.provider, model: llm.model, latency_ms: 0, error: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setTesting(false);
    }
  };

  const showOllama = ollama.configured || ollama.reachable || llm.embedding_provider === "ollama";
  const canDownload = ollama.configured && ollama.reachable && !ollama.cloud && ollama.model_pulled === false;
  const chosen = OLLAMA_SETUPS.find((s) => s.id === setup) ?? OLLAMA_SETUPS[0];
  const fallbacks = llm.providers.slice(1).map(providerLabel).join(", ");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Cpu className="h-4 w-4" /> AI model</CardTitle>
        <CardDescription>
          Scores jobs, tailors your resume, writes cover letters and answers questions. API keys live only in <code>.env</code> on the
          machine running the app. This page never asks for them or shows them.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5 text-sm">
        <div className="grid gap-4 sm:grid-cols-3">
          <Fact label="Provider">{providerLabel(llm.provider)}</Fact>
          <Fact label="Model"><span className="font-mono text-[13px]">{llm.model ?? "built-in heuristics"}</span></Fact>
          <Fact label="Fallback">{fallbacks || "none"}</Fact>
        </div>
        {!llm.provider && (
          <div className="flex items-start gap-2 text-muted-foreground">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <p>
              No AI model is set up, so every step runs on built-in heuristics (it works, at lower quality). Add a Claude key to{" "}
              <code>.env</code> or run a free model with Ollama, as shown below.
            </p>
          </div>
        )}

        {showOllama && (
          <div className="space-y-3 border-t border-border/70 pt-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="label-caps mr-1">Ollama</span>
              {ollama.reachable
                ? <Badge tone="success">Reachable{ollama.version ? ` · v${ollama.version}` : ""}</Badge>
                : <Badge tone="danger">Not reachable</Badge>}
              {ollama.cloud && <Badge tone="info">Cloud</Badge>}
              {ollama.model && ollama.model_pulled === true && <Badge tone="success">{ollama.cloud ? "Model available" : "Model downloaded"}</Badge>}
              {ollama.model && ollama.model_pulled === false && <Badge tone="warning">{ollama.cloud ? "Model not offered" : "Model not downloaded"}</Badge>}
            </div>
            <p className="break-words text-xs text-muted-foreground">
              <span className="font-mono">{ollama.base_url}</span>
              {ollama.model && <> · model <span className="font-mono">{ollama.model}</span></>}
              {!ollama.configured && ollama.reachable && <> · running, but the app isn&apos;t set up to use it yet (see below)</>}
            </p>
            {ollama.configured && ollama.error && (
              <p className="flex items-start gap-2 text-xs text-warning"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {ollama.error}</p>
            )}
            {canDownload && !pulling && (
              <div className="flex flex-wrap items-center gap-3">
                <Button size="sm" onClick={startPull} loading={starting}><Download /> Download model</Button>
                <span className="text-xs text-muted-foreground">One-time download of {ollama.model}; a few GB, so it can take a while.</span>
              </div>
            )}
            {pull && pull.status !== "idle" && (
              <div className="space-y-1.5" aria-live="polite">
                <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
                  <span className="label-caps">
                    {pull.status === "error" ? "Download failed" : pull.status === "success" ? `Downloaded ${pull.model}` : `Downloading ${pull.model}`}
                  </span>
                  <span className="tabular-nums text-muted-foreground">
                    {pull.total ? `${formatBytes(pull.completed)} of ${formatBytes(pull.total)} · ${pull.percent}%` : pull.detail}
                  </span>
                </div>
                <Progress value={pull.percent} aria-label="Model download progress" />
                {pulling && pull.detail && <p className="text-xs text-muted-foreground">{pull.detail}</p>}
                {pull.error && <p className="text-xs text-primary">{pull.error}</p>}
              </div>
            )}
          </div>
        )}

        <div className="space-y-3 border-t border-border/70 pt-4">
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" size="sm" onClick={runTest} loading={testing}><Zap /> Test AI</Button>
            {testing && (
              <span className="text-xs text-muted-foreground">
                Asking the model… On a server without a GPU the first answer can take a minute or two while the model loads.
              </span>
            )}
          </div>
          {result && (
            <div className={cn("border p-3", result.ok ? "border-success/60" : "border-primary/60")} role="status">
              <p className="flex flex-wrap items-center gap-2 font-medium">
                {result.ok ? <CheckCircle2 className="h-4 w-4 text-success" /> : <AlertTriangle className="h-4 w-4 text-primary" />}
                {result.ok ? `Answered in ${(result.latency_ms / 1000).toFixed(1)} s` : "The AI didn't answer"}
                {result.provider && (
                  <span className="font-normal text-muted-foreground">· {providerLabel(result.provider)}{result.model ? ` · ${result.model}` : ""}</span>
                )}
              </p>
              {result.sample && <p className="mt-2 italic">&ldquo;{result.sample}&rdquo;</p>}
              {result.error && <p className="mt-2 break-words text-muted-foreground">{result.error}</p>}
              {result.hint && <p className="mt-2 text-xs"><span className="label-caps mr-2 text-primary">Fix</span>{result.hint}</p>}
            </div>
          )}
        </div>

        <div className="space-y-3 border-t border-border/70 pt-4">
          <div>
            <p className="label-caps">How to set it up: free AI with Ollama</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Ollama runs open models on your own computer or server, for free. It&apos;s slower than Claude and its written answers
              are plainer, and you review every application before it&apos;s sent anyway.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {OLLAMA_SETUPS.map((s) => (
              <button key={s.id} type="button" className={pill(setup === s.id)} aria-pressed={setup === s.id} onClick={() => setSetup(s.id)}>{s.title}</button>
            ))}
          </div>
          <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
            {chosen.steps.map((step, i) => <li key={i}>{step}</li>)}
          </ol>
          <CopyBlock text={chosen.env} label=".env lines" />
          <p className="text-xs text-muted-foreground">
            Keys go only in <code>.env</code> on the machine that runs the app: never in this page, a chat or a command line. Restart the app
            after editing <code>.env</code>, then press <strong>Test AI</strong>.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function IntegrationsPanel() {
  const { data: integ, mutate } = useIntegrations();
  const { data: me, mutate: mutateMe } = useMe();
  const [token, setToken] = useState<{ token: string; api_url: string } | null>(null);
  const [webhooks, setWebhooks] = useState({ discord: "", slack: "" });
  const [channels, setChannels] = useState<string[]>([]);
  const [progress, setProgress] = useState<{ everywhere: boolean; digest: "daily" | "weekly" | "off"; popups: boolean }>({ everywhere: true, digest: "daily", popups: true });
  const { saving, run } = useSaver();
  const toast = useToast();
  useEffect(() => {
    if (me) {
      setWebhooks({ discord: me.preferences.discord_webhook_url || "", slack: me.preferences.slack_webhook_url || "" });
      setChannels(me.preferences.notification_channels);
      setProgress({ everywhere: me.preferences.progress_updates_everywhere ?? true, digest: me.preferences.progress_digest ?? "daily",
        popups: me.preferences.notification_popups !== false });
    }
  }, [me]);
  if (!integ) return null;

  const connectGoogle = () => run(async () => {
    const { url } = await api<{ url: string }>("/auth/google/connect");
    window.location.href = url;
  }, "Redirecting to Google…");

  return (
    <div className="space-y-6">
      <AIModelCard integ={integ} refresh={mutate} />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Mail className="h-4 w-4" /> Google — Gmail & Calendar</CardTitle>
          <CardDescription>Reads recruiter e-mails, labels them, drafts replies and creates interview events. Minimal scopes; revocable any time.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!integ.google.configured ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground"><AlertTriangle className="h-4 w-4 text-warning" /> Google OAuth isn&apos;t configured on this server (set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).</p>
          ) : integ.google.connected ? (
            <>
              <p className="flex items-center gap-2 text-sm"><CheckCircle2 className="h-4 w-4 text-success" /> Connected as {integ.google.email}</p>
              <div className="flex gap-2">
                <Badge tone={integ.google.gmail ? "success" : "muted"}>Gmail</Badge>
                <Badge tone={integ.google.calendar ? "success" : "muted"}>Calendar</Badge>
                {integ.google.push_enabled && <Badge tone="info">Push notifications</Badge>}
              </div>
              {integ.google.last_polled_at && <p className="text-xs text-muted-foreground">Inbox checked {timeAgo(integ.google.last_polled_at)}</p>}
              <Button variant="outline" size="sm" loading={saving} onClick={() => run(async () => { await post("/auth/google/disconnect"); await mutate(); }, "Google disconnected")}>Disconnect</Button>
            </>
          ) : (
            <Button onClick={connectGoogle} loading={saving}><Link2 /> Connect Google account</Button>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Puzzle className="h-4 w-4" /> LinkedIn — Chrome extension</CardTitle>
          <CardDescription>The extension securely syncs your LinkedIn session so the agent can use Easy Apply and keep your resume in sync.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {integ.linkedin.connected ? (
            <p className="flex items-center gap-2">
              {integ.linkedin.session_valid ? <CheckCircle2 className="h-4 w-4 text-success" /> : <AlertTriangle className="h-4 w-4 text-warning" />}
              {integ.linkedin.session_valid ? "Session synced" : "Session expired — re-sync from the extension"} · {timeAgo(integ.linkedin.updated_at)}
            </p>
          ) : <p className="text-muted-foreground">Not connected.</p>}
          <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
            <li>Load the <code>extension/</code> folder in Chrome (chrome://extensions → Developer mode → Load unpacked).</li>
            <li>Generate a token below and paste it, with the dashboard URL, into the extension popup.</li>
            <li>Log into LinkedIn, then click “Sync LinkedIn session”.</li>
          </ol>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => run(async () => setToken(await post("/auth/extension-token")), "Token generated")}><KeyRound /> Generate extension token</Button>
            {integ.linkedin.connected && (
              <Button variant="ghost" size="sm" onClick={() => run(async () => { await del("/users/me/integrations/linkedin"); await mutate(); }, "LinkedIn disconnected")}>Disconnect</Button>
            )}
          </div>
          {token && (
            <div className="space-y-2 bg-muted p-3">
              <p className="text-xs text-muted-foreground">Dashboard URL: <code>{window.location.origin}</code></p>
              <div className="flex gap-2">
                <Input readOnly aria-label="Extension token" value={token.token} className="font-mono text-xs" />
                <Button size="icon" variant="outline" aria-label="Copy token" onClick={() => { navigator.clipboard.writeText(token.token); toast({ title: "Copied", tone: "success" }); }}><Copy /></Button>
              </div>
              <p className="text-xs text-muted-foreground">This token can only sync your LinkedIn and Internshala sessions. It expires in 180 days.</p>
            </div>
          )}
        </CardContent>
      </Card>

      <InternshalaCard status={integ.internshala} onChange={mutate} />

      <Card>
        <CardHeader>
          <CardTitle>Notifications</CardTitle>
          <CardDescription>Everything always collects in the bell (top right). E-mail uses SMTP when configured, otherwise your connected Gmail.</CardDescription>
        </CardHeader>
        <CardContent>
          <Row label="Channels">
            <div className="flex flex-wrap gap-2">
              {["dashboard", "email", "discord", "slack"].map((c) => (
                <button key={c} type="button" onClick={() => setChannels(channels.includes(c) ? channels.filter((x) => x !== c) : [...channels, c])}
                  className={cn(pill(channels.includes(c)), "capitalize")} aria-pressed={channels.includes(c)}>{c}</button>
              ))}
            </div>
          </Row>
          <Row label="Pop-ups" hint="Off: nothing pops up, in the dashboard or as a system notification. Everything still collects in the bell (top right) and goes to your other channels. Also in the bell: Mute pop-ups.">
            <Switch checked={progress.popups} onCheckedChange={(v) => setProgress({ ...progress, popups: v })} label="Show notification pop-ups" />
          </Row>
          <Row label="Browser notifications" hint="System notifications while the dashboard is open in the background.">
            <Button variant="outline" size="sm" onClick={async () => {
              if (!("Notification" in window)) return toast({ title: "Not supported in this browser", tone: "error" });
              const result = await Notification.requestPermission();
              toast({ title: result === "granted" ? "Browser notifications enabled" : "Permission not granted", tone: result === "granted" ? "success" : "error" });
            }}>Enable browser notifications</Button>
          </Row>
          <Row label="Application updates everywhere" hint="Every update on a job you applied to (applied, reply, test, interview, offer, rejection) goes to Gmail, the dashboard and Discord/Slack, not just here.">
            <Switch checked={progress.everywhere} onCheckedChange={(v) => setProgress({ ...progress, everywhere: v })} label="Application updates everywhere" />
          </Row>
          <Row label="Progress e-mail" hint="A summary of every application you've made: what changed, what's waiting, who to follow up with. Sent at about 8 PM in your time zone.">
            <Select className="max-w-xs" value={progress.digest} onChange={(e) => setProgress({ ...progress, digest: e.target.value as typeof progress.digest })}>
              <option value="daily">Daily</option><option value="weekly">Weekly (Sundays)</option><option value="off">Off</option>
            </Select>
          </Row>
          <Row label="Discord webhook URL"><Input value={webhooks.discord} onChange={(e) => setWebhooks({ ...webhooks, discord: e.target.value })} placeholder="https://discord.com/api/webhooks/…" /></Row>
          <Row label="Slack webhook URL"><Input value={webhooks.slack} onChange={(e) => setWebhooks({ ...webhooks, slack: e.target.value })} placeholder="https://hooks.slack.com/services/…" /></Row>
          <div className="pt-4">
            <Button loading={saving} onClick={() => run(async () => {
              await put("/users/me/preferences", { preferences: { notification_channels: channels, discord_webhook_url: webhooks.discord || null, slack_webhook_url: webhooks.slack || null,
                progress_updates_everywhere: progress.everywhere, progress_digest: progress.digest, notification_popups: progress.popups } });
              await mutateMe();
              await mutate();
            })}><Save /> Save notifications</Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Agent capabilities</CardTitle><CardDescription>Server-side configuration (set via environment variables).</CardDescription></CardHeader>
        <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
          <p>AI model: <span className="font-medium">{integ.llm.provider ? `${providerLabel(integ.llm.provider)}${integ.llm.model ? ` · ${integ.llm.model}` : ""}` : "not configured — using built-in heuristics"}</span></p>
          <p>Embeddings: <span className="font-medium">{integ.llm.embedding_provider}</span></p>
          <p>Residential proxies: <span className="font-medium">{integ.automation.proxies || "none"}</span></p>
          <p>CAPTCHA solver: <span className="font-medium">{integ.automation.captcha ? "configured" : "not configured"}</span></p>
          <p>Auto-fill forms: <span className="font-medium">{integ.automation.auto_stage ? "on" : "off"}</span></p>
          <p>Dry-run mode: <span className="font-medium">{integ.automation.dry_run ? "ON (submit is never clicked)" : "off"}</span></p>
        </CardContent>
      </Card>
    </div>
  );
}

/** The opt-in Internshala apply bot: your synced Internshala login, a session check and the bot's switches. */
function InternshalaCard({ status, onChange }: { status: Integrations["internshala"]; onChange: () => Promise<unknown> }) {
  const { data: me, mutate: mutateMe } = useMe();
  const { saving, run } = useSaver();
  const toast = useToast();
  const [checking, setChecking] = useState(false);
  const [warning, setWarning] = useState(false);
  const [form, setForm] = useState({ bot: false, auto: false, limit: 15 });
  useEffect(() => {
    if (me) setForm({ bot: !!me.preferences.internshala_bot_enabled, auto: !!me.preferences.internshala_auto_submit,
      limit: me.preferences.internshala_daily_limit ?? 15 });
  }, [me]);
  const save = (next: typeof form) => run(async () => {
    const limit = Math.min(25, Math.max(1, Math.round(next.limit) || 15)); // 1-25 a day
    setForm({ ...next, limit });
    await put("/users/me/preferences", { preferences: {
      internshala_bot_enabled: next.bot, internshala_auto_submit: next.bot && next.auto, internshala_daily_limit: limit } });
    await Promise.all([mutateMe(), onChange()]);
  }, next.bot ? "Internshala bot settings saved" : "Internshala bot turned off");
  const check = async () => {
    setChecking(true);
    try {
      const { session_valid } = await post<{ session_valid: boolean }>("/users/me/integrations/internshala/check");
      toast(session_valid
        ? { title: "Your Internshala login works", tone: "success" }
        : { title: "Internshala session expired", description: "Open Internshala in Chrome and click Sync in the extension.", tone: "error" });
      await onChange();
    } catch (err) {
      toast({ title: "Could not check the session", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setChecking(false);
    }
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Bot className="h-4 w-4" /> Internshala — apply bot</CardTitle>
        <CardDescription>Optional. The agent fills Internshala applications with your own Internshala login (synced by the same Chrome extension) and sends them when you click Submit.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {status.connected ? (
          <p className="flex items-center gap-2">
            {status.session_valid ? <CheckCircle2 className="h-4 w-4 text-success" /> : <AlertTriangle className="h-4 w-4 text-warning" />}
            {status.session_valid ? "Login synced from the extension" : "Session expired — open Internshala in Chrome and click Sync in the extension"} · {timeAgo(status.updated_at)}
          </p>
        ) : <p className="text-muted-foreground">Not connected. Log into Internshala in Chrome, then click “Sync Internshala session” in the extension.</p>}
        {status.connected && (
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" loading={checking} onClick={check}><RefreshCw /> Check session</Button>
            <Button variant="ghost" size="sm" onClick={() => run(async () => { await del("/users/me/integrations/internshala"); await onChange(); }, "Internshala disconnected")}>Disconnect</Button>
          </div>
        )}
        <Row label="Let the agent apply on Internshala" hint="Off by default. When on, Internshala jobs you keep are filled in a real browser with your login, and wait for your click unless you turn on Submit automatically.">
          <Switch checked={form.bot} label="Let the agent apply on Internshala"
            onCheckedChange={(on) => (on ? setWarning(true) : save({ ...form, bot: false, auto: false }))} />
        </Row>
        <Row label="Submit automatically" hint="Off: every filled Internshala application waits for your Submit click. On: Internshala jobs you keep in Swipe Review are sent as soon as they're filled; anything the agent is unsure about still waits for you.">
          <Switch checked={form.bot && form.auto} disabled={!form.bot} label="Submit Internshala applications automatically"
            onCheckedChange={(on) => setForm({ ...form, auto: on })} />
        </Row>
        <Row label="Daily limit" hint="At most this many Internshala applications a day (1–25), a minute or more apart. Extra approved ones go out the next day.">
          <Input type="number" min={1} max={25} className="max-w-[8rem]" value={form.limit} disabled={!form.bot}
            onChange={(e) => setForm({ ...form, limit: e.target.value === "" ? 15 : Number(e.target.value) })} />
        </Row>
        <div className="pt-2"><Button onClick={() => save(form)} loading={saving} disabled={!form.bot}><Save /> Save Internshala settings</Button></div>
      </CardContent>
      <Modal open={warning} onOpenChange={setWarning} title="Let the agent apply on Internshala?" description="Read this before you turn it on."
        footer={<>
          <Button variant="ghost" onClick={() => setWarning(false)}>Cancel</Button>
          <Button loading={saving} onClick={async () => { if (await save({ ...form, bot: true })) setWarning(false); }}>I understand, turn it on</Button>
        </>}>
        <div className="space-y-3 text-sm">
          <p className="label-caps text-primary">Your Internshala account is at risk</p>
          <p>Internshala&apos;s terms don&apos;t allow automated access, and Internshala can restrict or suspend accounts it believes are automated. You use this at your own risk.</p>
          <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
            <li>The agent applies slowly: at most {form.limit} applications a day, a minute or more apart.</li>
            <li>You review every application before it&apos;s sent, unless you turn on Submit automatically.</li>
            <li>External listings and internships you already applied to are never sent; they&apos;re marked for you.</li>
            <li>You can turn it off here at any time.</li>
          </ul>
        </div>
      </Modal>
    </Card>
  );
}

function PrivacyPanel() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const { saving, run } = useSaver();
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader><CardTitle>Export your data</CardTitle><CardDescription>Download everything stored about you as JSON (GDPR / CCPA right to access).</CardDescription></CardHeader>
        <CardContent><a href="/api/v1/users/me/export" className={buttonVariants({ variant: "outline" })}><Download /> Download export</a></CardContent>
      </Card>
      <Card className="border-destructive/40">
        <CardHeader><CardTitle className="text-destructive">Delete my account</CardTitle><CardDescription>Permanently deletes your resumes (database and file storage), applications, e-mails, interviews, OAuth tokens and account. This cannot be undone.</CardDescription></CardHeader>
        <CardContent><Button variant="destructive" onClick={() => setOpen(true)}><Trash2 /> Delete everything</Button></CardContent>
      </Card>
      <Modal open={open} onOpenChange={setOpen} title="Delete your account?" description="Type DELETE to confirm."
        footer={<Button variant="destructive" disabled={confirmText !== "DELETE"} loading={saving}
          onClick={() => run(async () => { await del("/users/me", { confirm: "DELETE" }); router.replace("/"); }, "Account deleted")}>Delete permanently</Button>}>
        <Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} placeholder="DELETE" aria-label="Type DELETE to confirm" />
      </Modal>
    </div>
  );
}

function SettingsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const tab = params.get("tab") || "mass-apply";
  useEffect(() => {
    if (params.get("google") === "connected") router.replace("/dashboard/settings?tab=integrations");
  }, [params, router]);
  return (
    <div>
      <PageHeader eyebrow="Setup" title="Settings" description="Tell the agent what you want, where to look, and how to answer." />
      <Tabs value={tab} onValueChange={(v) => router.replace(`/dashboard/settings?tab=${v}`)}>
        <TabsList className="h-auto w-full flex-wrap">
          <TabsTrigger value="mass-apply">Mass apply</TabsTrigger>
          <TabsTrigger value="preferences">Preferences</TabsTrigger>
          <TabsTrigger value="sources">Job sources</TabsTrigger>
          <TabsTrigger value="answers">Saved answers</TabsTrigger>
          <TabsTrigger value="integrations">Integrations</TabsTrigger>
          <TabsTrigger value="profile">Profile</TabsTrigger>
          <TabsTrigger value="privacy">Privacy</TabsTrigger>
        </TabsList>
        <TabsContent value="mass-apply"><MassApplyPanel /></TabsContent>
        <TabsContent value="preferences"><PreferencesForm /></TabsContent>
        <TabsContent value="sources"><PreferencesForm sourcesOnly /></TabsContent>
        <TabsContent value="answers"><FieldMappingsForm /></TabsContent>
        <TabsContent value="integrations"><IntegrationsPanel /></TabsContent>
        <TabsContent value="profile"><ProfileForm /></TabsContent>
        <TabsContent value="privacy"><PrivacyPanel /></TabsContent>
      </Tabs>
    </div>
  );
}

export default function SettingsPage() {
  return <Suspense><SettingsInner /></Suspense>;
}
