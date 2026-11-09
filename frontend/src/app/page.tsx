import Link from "next/link";
import { ArrowUpRight, Check, Play, ShieldCheck, X } from "lucide-react";
import { BrushHeadline, Logo, Seal, TagPile, TunnelGrid } from "@/components/brand";
import { PipelineMotion } from "@/components/motion-graphics";
import { ThemeToggle } from "@/components/theme-toggle";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const SOURCES = ["Internshala", "Greenhouse", "Lever", "Ashby", "Workday", "LinkedIn", "Wellfound", "Indeed", "Simplify lists", "Career pages"];

const STEPS = [
  { n: "01", title: "Scan", text: "Thousands of live internships from curated GitHub lists, 110+ startup boards, LinkedIn, Indeed and any careers page — every 6 hours." },
  { n: "02", title: "Swipe", text: "Nothing is skipped behind your back. Every job lands in your deck with a match score: keep it or skip it in a second." },
  { n: "03", title: "Tailor & fill", text: "Each kept job gets a truthful tailored resume, a cover letter and a fully filled application form in a real browser." },
  { n: "04", title: "Apply & track", text: "Submitted automatically once every eligibility question has your saved answer. Replies and interviews are tracked from Gmail." },
];

const RULES = [
  { title: "You pick every job", text: "Kept jobs are the only ones prepared. Undo any swipe until preparation starts." },
  { title: "Never guesses eligibility", text: "Visa, work authorization and background questions stay blank until you answer them once." },
  { title: "Never invents experience", text: "A truthfulness guard reverts any skill, title, date or number that isn't on your resume." },
];

export default function Landing() {
  return (
    <main className="noise min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-[1440px] border-x border-line/70">
        {/* ---------------------------------------------------------------- header */}
        <header className="flex h-20 items-center justify-between border-b border-line/70 px-5 sm:px-10">
          <Logo />
          <nav className="flex items-center gap-1 sm:gap-7">
            <a href="#how" className="label-caps hidden text-[12px] text-foreground/85 hover:text-foreground md:inline">How it works</a>
            <a href="#swipe" className="label-caps hidden text-[12px] text-foreground/85 hover:text-foreground md:inline">Swipe</a>
            <a href="#rules" className="label-caps hidden text-[12px] text-foreground/85 hover:text-foreground md:inline">Safety</a>
            <Link href="/login" className="label-caps hidden text-[12px] text-foreground/85 hover:text-foreground sm:inline">Sign in</Link>
            <ThemeToggle />
            <Link href="/register" className={buttonVariants({ size: "default" })}>Get started <ArrowUpRight /></Link>
          </nav>
        </header>

        {/* ---------------------------------------------------------------- hero */}
        <section className="grid border-b border-line/70 lg:grid-cols-[1fr_minmax(0,500px)]">
          <div className="relative flex flex-col overflow-hidden px-5 pb-16 pt-14 sm:px-10 md:min-h-[640px] md:pb-40 lg:px-24 lg:pt-28">
            <p className="label-caps mb-8 flex items-center gap-2 text-muted-foreground">
              <span className="h-2 w-2 animate-pulse-dot rounded-full bg-primary" /> Internship season is live
            </p>
            <BrushHeadline lines={["Swipe right.", "We apply.", "Internships", "on autopilot"]} />
            <p className="mt-8 max-w-xl text-lg leading-relaxed text-foreground/80">
              AutoApply scans thousands of internships at startups and big tech, lets you keep or skip each one in a
              swipe, then tailors your resume, fills the form and applies — for every job you keep.
            </p>
            <div className="mt-10 flex flex-wrap items-center gap-8">
              <Link href="/register" className={buttonVariants({ size: "xl" })}>Start swiping</Link>
              <a href="#how" className="group inline-flex items-center gap-4">
                <span className="flex h-14 w-14 items-center justify-center rounded-full border border-foreground/60 transition-colors group-hover:border-primary group-hover:bg-primary">
                  <Play className="h-5 w-5 translate-x-0.5 fill-current" />
                </span>
                <span className="label-caps text-[13px] font-bold">See how it works</span>
              </a>
            </div>
            <TagPile className="absolute bottom-0 right-4 hidden origin-bottom-right scale-[0.8] md:block xl:scale-100" />
          </div>
          <div className="relative hidden min-h-[640px] overflow-hidden border-l border-line/70 lg:block">
            <TunnelGrid />
          </div>
        </section>

        {/* ---------------------------------------------------------------- stats row */}
        <section className="grid border-b border-line/70 md:grid-cols-3">
          <div className="flex flex-wrap items-center gap-x-8 gap-y-3 px-5 py-8 sm:px-10">
            {["Greenhouse", "Lever", "Ashby", "Workday"].map((s) => (
              <span key={s} className="text-xl font-bold tracking-tight text-foreground/90">{s}</span>
            ))}
          </div>
          <div className="flex items-center gap-5 border-t border-line/70 px-5 py-8 sm:px-10 md:border-l md:border-t-0">
            <div className="flex -space-x-3">
              {["bg-primary", "bg-foreground", "bg-muted-foreground", "bg-primary/70"].map((c, i) => (
                <span key={c} className={cn("flex h-11 w-11 items-center justify-center rounded-full border-2 border-background text-[11px] font-bold text-background", c)}>
                  {["GH", "LV", "AS", "WD"][i]}
                </span>
              ))}
            </div>
            <div>
              <p className="font-display text-2xl">4,000+</p>
              <p className="text-sm text-muted-foreground">live internships, refreshed daily</p>
            </div>
          </div>
          <div className="flex items-center gap-5 border-t border-line/70 px-5 py-8 sm:px-10 md:border-l md:border-t-0">
            <Seal />
            <p className="text-[17px] leading-snug">Nothing is sent for a job you didn&apos;t keep — and eligibility is never guessed.</p>
          </div>
        </section>

        {/* ---------------------------------------------------------------- marquee */}
        <div className="overflow-hidden border-b border-line/70 bg-primary py-4 text-primary-foreground">
          <div className="flex w-max animate-marquee gap-10 whitespace-nowrap motion-reduce:animate-none">
            {[...SOURCES, ...SOURCES].map((s, i) => (
              <span key={`${s}-${i}`} className="display flex items-center gap-10 text-2xl">{s}<span aria-hidden>✦</span></span>
            ))}
          </div>
        </div>

        {/* ---------------------------------------------------------------- how it works */}
        <section id="how" className="border-b border-line/70">
          <div className="flex flex-col justify-between gap-4 px-5 py-14 sm:px-10 md:flex-row md:items-end">
            <h2 className="display text-4xl sm:text-5xl">How it works</h2>
            <p className="max-w-md text-muted-foreground">Four steps, and only one of them needs you: the swipe.</p>
          </div>
          <div className="grid border-t border-line/70 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((s, i) => (
              <div key={s.n} className={cn("group p-8 transition-colors hover:bg-card sm:p-10", i > 0 && "border-t border-line/70 sm:border-t-0",
                i % 2 === 1 && "sm:border-l", i > 1 && "sm:border-t lg:border-t-0", i > 0 && "lg:border-l")}>
                <p className="font-display text-5xl text-primary">{s.n}</p>
                <h3 className="label-caps mt-8 text-sm font-bold text-foreground">{s.title}</h3>
                <p className="mt-3 leading-relaxed text-muted-foreground">{s.text}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ---------------------------------------------------------------- the loop (motion graphic) */}
        <section aria-labelledby="loop-title" className="border-b border-line/70">
          <div className="flex flex-col justify-between gap-4 px-5 pt-14 sm:px-10 md:flex-row md:items-end">
            <div>
              <p className="label-caps text-primary">The loop</p>
              <h2 id="loop-title" className="display mt-4 text-4xl sm:text-5xl">Always on.<br />Always tracking.</h2>
            </div>
            <p className="max-w-md text-muted-foreground">
              Summer 2027 internships, Delhi NCR first and India above all, flow through the agent every few hours. Applied
              somewhere yourself? Hit “I Applied” and it&apos;s tracked right alongside the rest.
            </p>
          </div>
          <PipelineMotion className="px-5 pb-14 pt-12 sm:px-10" />
        </section>

        {/* ---------------------------------------------------------------- swipe preview */}
        <section id="swipe" className="grid border-b border-line/70 lg:grid-cols-2">
          <div className="px-5 py-16 sm:px-10 lg:py-24">
            <p className="label-caps text-primary">Swipe Review</p>
            <h2 className="display mt-4 text-4xl sm:text-5xl">Keep. Skip.<br />Repeat.</h2>
            <p className="mt-6 max-w-lg text-lg leading-relaxed text-foreground/80">
              A deck of every internship that passed your filters, best matches first. Drag right to keep, left to skip,
              or use your arrow keys. Keep 50 at once with one click when you&apos;re in mass-apply mode.
            </p>
            <ul className="mt-8 space-y-3">
              {["Match score with strong and missing skills", "Visa sponsorship, location and term at a glance",
                "Undo any swipe until preparation starts"].map((t) => (
                <li key={t} className="flex items-center gap-3"><Check className="h-4 w-4 text-primary" /> {t}</li>
              ))}
            </ul>
          </div>
          <div className="relative flex min-h-[460px] items-center justify-center overflow-hidden border-t border-line/70 bg-card lg:border-l lg:border-t-0">
            <TunnelGrid className="absolute inset-0 opacity-40" animated={false} />
            <div className="relative h-[330px] w-[290px]">
              <div className="absolute inset-0 translate-x-5 translate-y-3 rotate-6 border border-line bg-background" />
              <div className="absolute inset-0 -translate-x-3 -rotate-3 border border-line bg-background" />
              <div className="absolute inset-0 flex flex-col border border-foreground/70 bg-background p-6">
                <div className="flex items-start justify-between">
                  <span className="label-caps text-muted-foreground">Ashby · Remote</span>
                  <span className="font-display text-3xl text-primary">86</span>
                </div>
                <p className="mt-6 font-display text-xl uppercase leading-tight">Software Engineer Intern</p>
                <p className="mt-1 text-muted-foreground">Ramp · Summer 2027</p>
                <div className="mt-5 flex flex-wrap gap-1.5">
                  {["Python", "React", "SQL", "Sponsors visas"].map((t) => (
                    <span key={t} className="rounded-full border border-foreground/40 px-2.5 py-0.5 text-xs">{t}</span>
                  ))}
                </div>
                <div className="mt-auto grid grid-cols-2 gap-2">
                  <span className="flex h-11 items-center justify-center gap-1 border border-foreground/50 text-xs font-bold uppercase tracking-wider"><X className="h-4 w-4" /> Skip</span>
                  <span className="flex h-11 items-center justify-center gap-1 bg-primary text-xs font-bold uppercase tracking-wider text-primary-foreground"><Check className="h-4 w-4" /> Keep</span>
                </div>
              </div>
              <span className="absolute -left-10 top-1/2 -rotate-12 border-2 border-primary bg-background px-3 py-1 font-display text-lg text-primary">KEEP</span>
            </div>
          </div>
        </section>

        {/* ---------------------------------------------------------------- rules */}
        <section id="rules" className="grid border-b border-line/70 md:grid-cols-3">
          {RULES.map((r, i) => (
            <div key={r.title} className={cn("p-8 sm:p-10", i > 0 && "border-t border-line/70 md:border-l md:border-t-0")}>
              <ShieldCheck className="h-6 w-6 text-primary" />
              <h3 className="label-caps mt-6 text-sm font-bold">{r.title}</h3>
              <p className="mt-3 leading-relaxed text-muted-foreground">{r.text}</p>
            </div>
          ))}
        </section>

        {/* ---------------------------------------------------------------- CTA */}
        <section className="flex flex-col items-start justify-between gap-8 px-5 py-16 sm:px-10 md:flex-row md:items-center">
          <h2 className="display text-4xl sm:text-6xl">Your next<br /><span className="text-primary">internship</span> is<br />one swipe away</h2>
          <Link href="/register" className={buttonVariants({ size: "xl" })}>Create your agent <ArrowUpRight /></Link>
        </section>
        <footer className="flex flex-col justify-between gap-2 border-t border-line/70 px-5 py-6 text-sm text-muted-foreground sm:flex-row sm:px-10">
          <Logo className="text-base text-foreground" />
          <p>Self-hosted · your data stays on your server · MIT licensed</p>
        </footer>
      </div>
    </main>
  );
}
