<p align="center">
  <img src="frontend/public/icon.svg" width="84" alt="AutoApply AI logo" />
</p>

<h1 align="center">AutoApply AI</h1>

<p align="center"><b>Swipe right. We apply. Internships on autopilot.</b></p>

An autonomous job-application agent that you run yourself, built for mass-applying to internships
and startup roles. It scans thousands of openings, puts every one that passes your filters into a
**Swipe Review** deck, and for each job you keep it tailors your resume, writes a cover letter, fills
in the application form in a real browser and submits it. After you apply, it watches your Gmail for
replies, updates each application's status, puts interviews on your Google Calendar with prep notes,
and shows you what is working.

```bash
./start.sh          # one command: installs what's missing and runs everything → http://localhost:3000
```

> **Nothing is sent for a job you didn't keep, and eligibility is never guessed.** Scans never skip a
> job for a low score; you decide in Swipe Review. Keeping a job is your approval to apply. If the
> form asks something only you can answer (visa sponsorship, work authorization, background checks)
> and you haven't saved an answer, the filled form stops in **Needs approval** instead. Turn off
> **Apply automatically** to review every filled form before it's submitted.

| Landing | Swipe Review |
|---|---|
| ![Landing](docs/screenshots/landing.png) | ![Swipe Review](docs/screenshots/swipe-review.png) |
| **Overview** | **Review a filled application** |
| ![Overview](docs/screenshots/overview.png) | ![Review](docs/screenshots/review.png) |
| **Mass-apply settings** | **Analytics** |
| ![Mass apply](docs/screenshots/mass-apply.png) | ![Analytics](docs/screenshots/analytics.png) |

---

## Contents

- [One-command start](#one-command-start)
- [What it does](#what-it-does)
- [Swipe Review and mass applying](#swipe-review-and-mass-applying)
- [Internships in India: Summer 2027, Delhi NCR first](#internships-in-india-summer-2027-delhi-ncr-first)
- ["I Applied" and progress tracking](#i-applied-and-progress-tracking)
- [Fast scans with a live progress bar](#fast-scans-with-a-live-progress-bar)
- [How forms get filled](#how-forms-get-filled)
- [Ready to submit: check it, fix it, send it](#ready-to-submit-check-it-fix-it-send-it)
- [How it works](#how-it-works)
- [Free AI with Ollama](#free-ai-with-ollama)
- [Quick start (Docker)](#quick-start-docker)
- [Try the whole loop safely with the demo careers site](#try-the-whole-loop-safely-with-the-demo-careers-site)
- [Using it for real](#using-it-for-real)
- [Connect Gmail and Google Calendar](#connect-gmail-and-google-calendar)
- [LinkedIn, Internshala and the Chrome extension](#linkedin-internshala-and-the-chrome-extension)
- [Local development without Docker](#local-development-without-docker)
- [Deployment](#deployment)
- [Testing and CI](#testing-and-ci)
- [Project structure](#project-structure)
- [Security and privacy](#security-and-privacy)
- [Responsible use](#responsible-use)
- [Troubleshooting](#troubleshooting)

---

## One-command start

```bash
git clone https://github.com/saksham-eng560/autoapply-ai.git && cd autoapply-ai
./start.sh                 # macOS / Linux / WSL — needs Python 3.11+ and Node 20+
```

The first run creates `.env` with fresh secrets, a Python virtualenv, installs Chromium and the npm
packages, and prepares a local SQLite database. Later runs start in seconds and only reinstall when
requirements change. It then runs the API (:8000), the dashboard (:3000) and the task queue, opens
your browser and streams the logs. **Ctrl-C stops everything.**

| Command | What it does |
|---|---|
| `./start.sh` | Local mode, no Docker. Uses Redis + a Celery worker + beat if `redis-server` is installed, otherwise runs tasks in-process with a built-in scheduler (scheduled scans still happen). |
| `./start.sh --demo` | Also seeds a demo account (`demo@example.com` / `demo-password-123`) with a swipe deck, and serves the demo careers site on :8765. |
| `./start.sh --prod` | Production build of the dashboard (faster pages). |
| `./start.sh --docker` | The full Docker Compose stack (PostgreSQL + pgvector, Redis, worker, beat). `./start.sh --stop` stops it. |
| `./start.sh --reset` | Wipes the local database first. |
| `./start.sh --clear-jobs` | Clears the job postings you haven't kept or applied to (Swipe Review, skipped, failed), so the next scan starts fresh. Your account, resume, settings and everything you applied to stay. `backend/.venv/bin/python scripts/clear_jobs.py` shows the counts first. |
| `./start.sh --ollama` | Sets up a free AI model on this computer with Ollama: checks it, downloads the model, fills in `.env` (see [Free AI with Ollama](#free-ai-with-ollama)). After that, every `./start.sh` starts Ollama and loads the model by itself. |
| `start.bat` | Windows: the Docker path (`start.bat stop` to stop). Or use WSL and `./start.sh`. |

Your first ten minutes:

1. **Create your account** at http://localhost:3000/register.
2. **Upload your resume** in Resume Lab (PDF, DOCX or pasted text) and check the parsed result.
3. **Settings › Mass apply › AI + Python · 2nd year**: one click sets the whole search (see below).
4. **Settings › Saved answers**: save work authorization and visa sponsorship. Without them, kept jobs
   stop in Needs approval instead of being submitted.
5. **Scan for jobs now**, then open **Swipe Review** and start swiping.

Add `ANTHROPIC_API_KEY=...` to `.env` for much better scoring, tailoring and answers, or run
`./start.sh --ollama` for a free model on your own computer (everything also works without either, on
built-in heuristics). `make start` does the same as `./start.sh`.

## What it does

**Discovery.** Scans Greenhouse, Lever, Ashby and Workday boards through their public APIs, reads
company careers pages (schema.org `JobPosting` markup, and embedded ATS boards it detects), and
searches LinkedIn, Indeed, Glassdoor and Wellfound with a real browser. It removes duplicates across
sources, and scans run on a schedule (every 6 hours by default) or when you click **Scan for jobs now**.
You can also paste any job URL.

**Matching.** Each job gets a 0–100 score across five parts: skills, experience, industry, location
and compensation. The score also explains which of your skills are strong matches and which the job
wants that you don't have. Only your hard filters remove a job automatically (companies to avoid,
excluded title keywords, job types, expired deadlines and, optionally, postings that don't sponsor
visas). Everything else — even a low score — goes to **Swipe Review**, best matches first, with
heads-ups such as "not a remote role". (The original automatic mode, which skips jobs under a
threshold and prepares the rest, is still available in Settings.)

**Your resume, your way.** By default the agent sends **your original resume file, unchanged**.
Switch to *light tweaks* (every word kept, relevant items moved to the top) or *full AI tailoring*
in Settings › Mass apply. With full tailoring, your master resume is rewritten for each job: the summary, bullet order,
emphasis and wording (for example, using the job's name for a skill you really have). A guard checks
the result against your master resume and reverts anything that adds an employer, title, date,
degree, skill or number that isn't in the original. You get an ATS-friendly PDF (classic or modern
template) and a cover letter that references the actual role.

**Form filling.** Dedicated submitters for Greenhouse, Lever, Workday and LinkedIn Easy Apply, and a
generic engine that handles any other HTML form: text, selects, radios, checkboxes, file uploads and
multi-step flows. Answers come from your saved answers, your resume and deterministic rules first,
then from the AI for open-ended questions. Eligibility questions such as visa sponsorship and work
authorization are **never guessed**. If you haven't saved an answer, the question stays blank and
flagged until you answer it, and the agent remembers your answer for next time. EEO questions default
to "decline to self-identify". Browsing uses human-like typing, mouse paths and pacing, and supports
proxy rotation and CAPTCHA solving (2Captcha / Anti-Captcha). Per-platform rate limits are enforced.

**Swipe, then submission.** Keep a job and it's prepared and — with **Apply automatically** on —
submitted as soon as the form is filled, provided every eligibility question has your saved answer.
Anything else waits in **Needs approval** with the filled-form screenshot, tailored resume, cover
letter, answers and match analysis. Submission happens
in a fresh browser session that re-fills the form with what you approved. The agent checks for a
confirmation page, saves a screenshot, and records the confirmation number when there is one.
`SUBMISSION_DRY_RUN=true` disables the final click entirely.

**Inbox and calendar.** With Google connected, the agent reads job-related e-mail (Pub/Sub push or
polling every 5 minutes) and classifies each message: rejection, interview invite, assessment, offer,
information request, or acknowledgement. It links the message to the right application and updates
its status. Messages are filed under `AutoApply AI/…` Gmail labels, and reply drafts are prepared
(never sent) for invites and requests. Interviews are added to Google Calendar with a prep document
covering the company, likely questions, STAR stories drawn from your resume, and questions to ask.
Reminders go out before each interview.

**Dashboard.** Next.js dashboard with a landing page, an overview, **Swipe Review**, an applications
pipeline, a job browser, an e-mail feed, interviews, analytics (response, interview and offer rates,
time to response, platform effectiveness, keywords that get callbacks), a resume editor, agent logs,
and settings. Live updates arrive over WebSocket. It works on phones, can be installed as a PWA, and
sends browser notifications.

**Design.** An editorial "ink" theme (charcoal, warm cream type and one signal red) with a "paper"
light theme: Dela Gothic One display type, Space Grotesk for the interface, hairline grid lines,
square controls and outlined pill tags. It's built from [shadcn/ui](https://ui.shadcn.com) (Radix)
components restyled through the design tokens in `frontend/src/app/globals.css`, with framer-motion
for the swipe deck, page transitions (a red sweep across the top on every page change), staggered
lists, counting numbers, the sliding sidebar highlight and the landing-page pipeline graphic. Every
animation turns off when your system's "reduce motion" setting is on. The logo, favicon, PWA and extension icons all use the same bracket-and-red-block
mark (`frontend/public/icon.svg`).

**Notifications.** In-app, browser, e-mail (SMTP or your own Gmail), Discord and Slack. Every update on
a job you applied to (submitted, "I Applied", reply, test, interview, offer, rejection) goes to all of
them, and a progress e-mail summarises everything each evening (see [below](#i-applied-and-progress-tracking)).

**Your data.** Export everything as JSON, or delete your account with all files and tokens (GDPR/CCPA).
OAuth tokens and cookies are encrypted with AES-256-GCM, and closed applications older than the retention period are
cleaned up automatically.

## Swipe Review and mass applying

**Swipe Review** (`/dashboard/review`) is a deck of every job that passed your filters, best matches
first. Each card shows the role, company, location, term, salary, visa sponsorship, the score
breakdown, the skills you have and the ones they want.

- **Drag right** or press **→** to keep: the agent tailors, fills and applies.
- **Drag left** or press **←** to skip.
- **Z** undoes the last swipe (until preparation has started).
- **A** or **I Applied to this myself**: you already applied on your own. The card flies off the deck,
  moves to Applied and is tracked from then on.
- **Keep in bulk**: keep every card at or above a score in one click (with the current filters).
- Filters: search, internship / full-time, remote only. A counter shows how many are left.

**The preset** (Settings › Mass apply): **AI + Python · 2nd year** sets every search filter in one click and
removes ones left over from earlier settings:

- AI engineer, generative AI, LLM and agent internships first in Swipe Review, then Python / FastAPI backend ones.
  Java, PHP, .NET, Node.js, frontend, mobile, data-analyst and non-tech roles are left out.
- Internships of at most 2 months that a 2nd-year student can apply to (no final-year-only, new-grad or
  full-time roles). Postings that don't state a length are kept.
- Startups and good companies only: renowned companies, startups posting on their own job board, and
  companies you mark legit.
- Delhi NCR first, then the rest of India and remote roles. Nothing from the US or abroad: the US
  internship lists and US startup boards aren't searched.
- Sources: Internshala, LinkedIn, Indeed, Wellfound and the top-companies list.

Your account, resume, saved answers, trusted / avoided companies and notifications are not changed.

Mass-apply settings (all in Settings › Mass apply):

| Preference | Default | Meaning |
|---|---|---|
| `resume_strategy` | `original` | Which resume is sent. `original`: your uploaded file, byte for byte (your design and words). `light`: every word kept, only the most relevant bullets, projects and skills moved to the top per job. `full`: AI rewrite, guarded against invented facts. |
| `review_mode` | `swipe` | `swipe`: nothing is skipped for a low score. `auto`: the original threshold mode. |
| `auto_submit_kept` | on | Submit kept jobs as soon as the form is filled. Off: every kept job waits for approval. |
| `trust_generated_answers` | on | The agent's answers to open questions ("Why this company?") don't hold a kept job back. Eligibility questions are never guessed either way. |
| `auto_keep_min_score` | off | Keep jobs scoring at least this without swiping. |
| `exclude_no_sponsorship` | off | Skip postings that say they don't sponsor visas or require citizenship. |
| `max_jobs_per_source` | 50 | How many postings each source may return per scan (10–1000). |
| `sources.internship_lists` | SimplifyJobs + vanshb03 | Curated lists: `simplify-internships`, `vanshb03-internships`, `simplify-new-grad`, or any `listings.json` URL in the same format. |

Daily and per-platform caps (e.g. 40 Greenhouse, 10 Workday applications a day, with randomized
cool-downs) still apply to every submission.

## Internships in India: Summer 2027, Delhi NCR first

Out of the box the agent hunts **internships only**, for **Summer 2027**, with **about 90% of every scan
in India** and **Delhi NCR** (Delhi, New Delhi, Gurugram, Noida, Greater Noida, Faridabad, Ghaziabad)
as the prime location. All of it is in Settings › Preferences › Internship focus.

- **Where it looks.** [Internshala](https://internshala.com) (India's biggest internship board, searched
  by your roles in your prime cities, work-from-home and all of India; paste your own Internshala search
  URLs in Settings › Job sources), LinkedIn searched for "Delhi, India" and "India", Indeed and
  Glassdoor on their Indian sites (`in.indeed.com`, `glassdoor.co.in`), plus the Summer 2027 GitHub lists
  and startup boards for the remaining ~10%.
- **What you see first.** Swipe Review shows prime-city internships first, then the rest of India, then
  remote, then abroad; within each group, postings that name Summer 2027 come first. Cards carry
  **Prime location**, **India** and **Summer 2027** badges.
- **~90% India.** After each scan the agent keeps roughly 9 Indian postings for every 1 from elsewhere
  (remote roles preferred). Cities such as "Bengaluru, Karnataka" count as India even when the listing
  doesn't say so. Change the share with the slider (50–100%) or switch the focus off.
- **Summer 2027.** Postings clearly for another term ("Summer 2026", "Fall '26", "Intern 2026") are
  skipped. Ones that don't say are kept, and ones that start immediately get a heads-up.
- **Intern roles only, for your year.** Every source (big tech, top companies, Internshala, LinkedIn,
  the lists) keeps internships only: full-time, new-grad and contract jobs, and full-time jobs about
  interns ("Intern Program Manager"), never reach your deck. With your year of study (default 2nd year)
  and graduation year (read from your resume, or set it), internships for other students are skipped
  too: final-year or pre-final-year only, "rising seniors", PhD / Master's / MBA only, another graduating
  batch, or "2+ years of experience". Ones that name your year (Google STEP, "1st and 2nd year
  students") are tagged **Open to 2nd-year students**. Settings › Preferences › Search preferences.
- **Your tech focus.** With **Tech focus** (e.g. AI, LLMs, Python, FastAPI) a posting must name one of
  them; with **Skip these technologies** (e.g. Java, Spring Boot) a role whose title names one, or that
  asks for one and none of your languages or frameworks, is left out. "Java or Python" roles and big tech
  software internships stay. Cards already waiting that don't fit are skipped with the reason.
- **At most 10 from Internshala a scan.** Each scan adds the best 10 new Internshala postings at most
  (and never more than 25% of the scan), so they don't crowd out the rest. Settings › Mass apply.
- **Internshala applications** need your own Internshala login. By default the agent prepares your
  resume and answers and asks you to apply there; click **I Applied** afterwards and it's tracked. If
  you turn on the opt-in **Internshala bot** (see
  [LinkedIn, Internshala and the Chrome extension](#linkedin-internshala-and-the-chrome-extension)),
  the agent fills the Internshala form itself with your synced login and sends it when you click
  **Submit**.

Internshala changes its pages from time to time. Check the scraper on your server with
`backend/.venv/bin/python scripts/test_scraper.py internshala -k "Software Engineer" -l Delhi`.

## "I Applied" and progress tracking

Applied to something on your own? Click **I Applied**. It's on every job in All jobs, every card in
Applications, the application page and Swipe Review (key **A**). The job moves to **Applied** and into
its own section, **I Applied** (`/dashboard/applied`), and the agent stops working on it:

- It watches your Gmail for replies from that company and updates the status by itself (applied →
  heard back → interview → offer, or closed). Nothing ever moves backwards.
- Every update is sent **everywhere**: the dashboard and browser, your Gmail, and Discord/Slack if
  connected ("Application status: applied → interview (SDE Intern @ Zomato)").
- A **progress e-mail** at about 8 PM your time (daily by default; weekly or off in Settings ›
  Integrations › Notifications) lists what changed, where everything stands, applications with no
  reply after 7 days (time for a polite follow-up) and interviews this week. **Progress e-mail** on the
  I Applied page sends one right away.
- **Log an application** adds one the agent never found (a referral, a company site): company, role,
  link, date and notes.

The I Applied page shows how far your applications got (applied, heard back, interviewing, offers),
lets you filter and search them, and update a status by hand when a recruiter calls instead of e-mailing.

## Fast scans with a live progress bar

A scan searches all your job sources **at the same time** and loads company boards (Greenhouse,
Lever, Ashby, Workday, career pages) several at once. Postings it has already saved are not
downloaded again, and Claude scores several jobs at once while the rest get an instant score. Cards
land in Swipe Review as they're scored, so you can start swiping before the scan ends.

While a scan runs, Overview and Swipe Review show a **progress bar**:
- the percentage, time elapsed and time left;
- the step it's on (search → save → score → done);
- each source with its status and how many postings it found;
- a **Stop** button. Jobs already scored stay in your deck.

Every other page shows a compact "Scanning 42%" in the top bar.

A slow site never holds up a scan. Each source wraps up with what it has at 80% of
`SCAN_SOURCE_TIMEOUT_SECONDS` (240 s by default). One still running at the limit is left out of that
scan, and the rest carry on. The speed settings are optional `.env` entries, and the defaults suit
most setups:

| Setting | Default | What it controls |
|---|---|---|
| `SCAN_SOURCE_CONCURRENCY` | 8 | job sources searched at the same time |
| `SCRAPER_BOARD_CONCURRENCY` | 6 | company boards or pages loaded at once within a source |
| `SCAN_LLM_CONCURRENCY` | 6 | jobs Claude scores at the same time |
| `SCAN_SOURCE_TIMEOUT_SECONDS` | 240 | time limit for a single source |

## How forms get filled

For every field on an application form, the agent picks the right way to fill it and then **checks
the page kept the value**. If it didn't, for example a React form that ignored the typing, it sets the
value again the way the page expects.

- **Dropdowns, radio buttons and suggestion lists** match the way forms word things. "India" picks
  "India (+91)", not "British Indian Ocean Territory". "B.Tech" picks "Bachelor's Degree", "USA"
  picks "United States" and "Bangalore" picks "Bengaluru". When no option really fits, the field is
  left for you in Needs approval instead of taking a wrong answer.
- **Dates and numbers** are typed the way the field expects. "2 weeks" becomes a real start date in a
  date picker, "₹15,000" becomes `15000` in a number box, and a `DD/MM/YYYY` box gets that format.
- **Length limits** are respected. A 300-character box gets an answer cut at a sentence or word
  boundary.
- **Follow-up questions** that appear after an answer ("If yes, please explain", a city after a
  country) are found on a second look and filled too.
- **Education and location questions** are answered from your resume: college, degree, branch,
  graduation year, CGPA, city, country and phone country code. You can override any of them under
  **Settings › Saved answers**.
- **Facts are never guessed.** Date of birth, ID numbers, visa and work authorization are filled only
  from your saved answers. Otherwise the application waits for you.
- **Written answers** ("Why this internship?") are specific, grounded in your resume and the job,
  and fit the field's length limit.

## Ready to submit: check it, fix it, send it

When the agent has filled an application and is waiting for you, it appears in **Ready to submit**
(`/dashboard/submit`, in the sidebar with a count). Applications show one at a time, best match first,
with a screenshot of the filled form. Anything that needs your attention is at the top: a required
question it couldn't answer, a low-confidence answer, a field it couldn't fill.

Each prefilled item has two buttons:

- **✓ Correct** confirms it.
- **✗ Fix** turns it into an edit box right there. What you type is exactly what gets sent.

The cover letter is editable in place, and the resume links to the PDF that will be uploaded. When
every row is checked, press **Submit application**. The agent opens the form again, fills it with what
you approved, including your fixes, and submits. Then the next application loads.

| Key | Action |
|---|---|
| `Y` | Confirm the selected row and move to the next |
| `N` | Fix the selected row |
| `J` / `K` | Move down / up |
| `A` | Confirm all rows that aren't flagged |
| `Enter` | Submit, once everything is confirmed |
| `S` | Skip for now |

Your fixes are learned. Corrected answers to common questions (notice period, relocation, highest education
and work authorization) are saved, so they come pre-filled correctly next time.

Internshala postings are submitted here too once the [Internshala bot](#the-internshala-bot-opt-in) is
on. Otherwise the card shows **Apply on Internshala** with your prepared answers (each with a **Copy**
button) and **I Applied** for afterwards.

## How it works

```
                 ┌──────────────────────────── Next.js dashboard (:3000) ─────────────────────────────┐
  you ──────────▶│ overview · swipe review · applications · jobs · e-mail · interviews · analytics ·… │
                 └──────────────┬───────────────────────────────────────────────▲─────────────────────┘
                   /api/v1/* (same-origin proxy)                                │ WebSocket (live updates)
                 ┌──────────────▼───────────────────────────────────────────────┴─────────────────────┐
                 │ FastAPI (:8000)  auth · resumes · jobs · applications · agent · e-mail · analytics │
                 └──────┬──────────────────────────┬───────────────────────────────┬──────────────────┘
                        │ PostgreSQL 16 + pgvector │ Redis (queue, rate limits,    │ Local disk / S3 / R2
                        │                          │ pub-sub for live updates)     │ (PDFs, screenshots)
                 ┌──────▼──────────────────────────▼───────────────────────────────▼──────────────────┐
                 │ Celery worker + beat   scan → match → ⏸ swipe → tailor → fill (Playwright) → submit │
                 │                        → Gmail monitor → calendar → reminders → analytics          │
                 └─────────────┬───────────────────────────┬──────────────────────────┬───────────────┘
                   Claude or Ollama (JSON-schema            Job boards & ATS          Gmail · Calendar
                   outputs; offline heuristics fallback)    (APIs + Chromium)         (Google OAuth)
```

The application lifecycle is `discovered → matched (waiting for your swipe) → preparing → approved →
applied → acknowledged → interview → offer / rejected`, with `pending approval` in between whenever a
question needs you (or you turned off **Apply automatically**). Every change is recorded in the
application's history.

**AI.** Claude is the primary model (`ANTHROPIC_MODEL`, default `claude-opus-5-5`), using JSON-schema
structured outputs, prompt caching and a refusal fallback. OpenAI is an optional secondary provider.
**Ollama** runs a free open model on your own computer or server, or on Ollama Cloud, with JSON-schema
constrained answers (see [Free AI with Ollama](#free-ai-with-ollama)). `LLM_PROVIDER` picks which one
goes first; the others are fallbacks. **Without any of them, every step still works** on built-in heuristics: keyword-based resume parsing,
rule-based scoring, template tailoring and cover letters, and rule-based e-mail classification. The
quality is lower but it's fully functional. All prompts are plain text files in [`prompts/`](prompts).

## Free AI with Ollama

[Ollama](https://ollama.com) runs open AI models on your own computer or server, for free and with no
API key. AutoApply talks to it through Ollama's own API with JSON-schema constrained answers, so job
scoring, resume tailoring, cover letters and form answers all work without Claude. Claude is still the
better writer: with `LLM_PROVIDER` left empty (auto), a Claude key goes first and Ollama becomes the
fallback; `LLM_PROVIDER=ollama` puts Ollama first.

**Which model.** Pick one that fits in memory. Download sizes are approximate, and the speeds are rough
estimates for a CPU-only server such as Oracle's free 4-core ARM machine. An Apple Silicon Mac answers
in seconds rather than minutes.

| `OLLAMA_MODEL` | Download | Quality | Rough time per answer, 4 ARM cores, no GPU |
|---|---|---|---|
| `qwen3.5:4b` (default) | ~3.4 GB | Good scores and answers, plain writing | 1–2 min |
| `qwen3.5:9b` | ~6.6 GB | Better writing and reasoning | 2–4 min |
| `qwen3:4b` / `qwen3:8b` | ~2.5 / ~5.2 GB | Proven alternatives | similar to the above |
| `llama3.2:3b` | ~2 GB | Fastest, weakest on long resumes | under 1 min |

### On your Mac

1. Install the Ollama app from https://ollama.com/download (or `brew install ollama`) and open it
   once. It serves on `http://localhost:11434`.
2. Run `./start.sh --ollama`. It checks that Ollama is running, downloads `qwen3.5:4b` once (showing
   progress), and adds `LLM_PROVIDER=ollama` and `OLLAMA_MODEL=qwen3.5:4b` to `.env` only if those are
   missing or empty. It never overwrites a value you set or touches any other line.
3. Open **Settings › Integrations › AI model** and press **Test AI**.

From then on a plain `./start.sh` starts Ollama for you (the Mac app, or `ollama serve` on Linux) and
loads your model, the same as `ollama run <model>` without the chat, so the first AI answer doesn't wait
for it. If the model can't load (for example "failed to initialize the Metal library" after a macOS or
Ollama update), it says so at startup with the fix, and the app keeps running on built-in heuristics.

The Docker stack (`./start.sh --docker --ollama`) uses the same Ollama app: inside a container,
`localhost` in `OLLAMA_BASE_URL` automatically means your Mac (`host.docker.internal`).

### On your Oracle server

Re-run the setup script with `WITH_OLLAMA=1`:

```bash
curl -fsSL https://raw.githubusercontent.com/saksham-eng560/autoapply-ai/main/scripts/server-setup.sh | WITH_OLLAMA=1 bash
# another model:  ... | WITH_OLLAMA=1 OLLAMA_MODEL=qwen3.5:9b bash
```

The script keeps your `.env` and only fills in what's missing:

- `COMPOSE_PROFILES=ollama` starts the `ollama` container (it's off by default);
- `LLM_PROVIDER=ollama`;
- `OLLAMA_BASE_URL=http://ollama:11434`;
- `OLLAMA_MODEL`.

It then downloads the model inside the container and prints the disk space it uses. Models live in the
`ollama-models` volume, so updates don't download them again. Ollama's API has no password, so its
port (11434) is never published: only the app's own containers can reach it. With 24 GB of RAM, the
4B and 9B models fit next to the app comfortably.

To do it by hand, add those four lines to `~/autoapply-ai/.env` and run
`docker compose -f docker-compose.prod.yml up -d`. Then press **Download model** in Settings, or run
`docker compose -f docker-compose.prod.yml exec ollama ollama pull qwen3.5:4b`.

### Ollama Cloud

Ollama also runs much bigger models on its own servers. Create an API key at
https://ollama.com/settings/keys, then open `.env` in an editor and add:

```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=https://ollama.com
OLLAMA_MODEL=gpt-oss:120b
OLLAMA_API_KEY=            # paste the key here, in .env only
```

Restart the app. `OLLAMA_API_KEY` is only for Ollama Cloud; a local Ollama needs no key. Keep it in
`.env`, never on the command line or in the dashboard. Cloud models don't support JSON-schema output,
so the app asks for JSON in the prompt and checks every answer, retrying once. The free plan runs one
request at a time, which is what `OLLAMA_CONCURRENCY=1` does. You can also use cloud models through a
local Ollama after `ollama signin`, with model names ending in `-cloud`.

### Test it

**Settings › Integrations › AI model** shows:

- the active provider and model, and the fallbacks;
- whether Ollama is reachable, its version, and whether the model is downloaded. If it isn't, a
  **Download model** button downloads it with a progress bar;
- a **Test AI** button that sends one small request and shows the answer and how long it took;
- copyable `.env` lines for each setup above.

The page never asks for keys or shows them. Edit `.env` and restart instead.

### What to expect

- **Slower.** Without a GPU an answer takes one to a few minutes. A scan gives the best
  `OLLAMA_MAX_EVALUATIONS_PER_SCAN` (15) jobs to the model one at a time (`OLLAMA_CONCURRENCY=1`) and
  scores the rest instantly with the built-in heuristic. Cards reach Swipe Review as they're scored.
  Preparing a kept job (resume, cover letter, answers) takes several minutes.
- **Plainer writing.** Small models score jobs well, but their cover letters and written answers are
  less polished than Claude's. Now and then a reply misses the requested format: the app asks once
  more, then falls back to heuristics. Every application goes through review, so you can fix anything
  before it's submitted.
- **Context.** `OLLAMA_NUM_CTX` (8192 tokens) is how much the model reads at once; Ollama's own
  default is only 4096. Long job descriptions and resumes are shortened to fit. Raise it (for example
  to 16384) if you have RAM to spare.
- **Embeddings (optional).** `EMBEDDING_PROVIDER=ollama` uses `nomic-embed-text`
  (`ollama pull nomic-embed-text`) instead of the built-in keyword matcher. After switching providers,
  re-compute the stored vectors with `python scripts/migrate.py --reembed` (in Docker:
  `docker compose exec api python scripts/migrate.py --reembed`).

Every setting is described in the "Free AI with Ollama" block of [`.env.example`](.env.example).

## Quick start (Docker)

Requirements: Docker with Compose v2, about 4 GB RAM free.

```bash
git clone https://github.com/saksham-eng560/autoapply-ai.git
cd autoapply-ai
scripts/init-env.sh      # creates a private .env with freshly generated secrets
```

Open `.env` in a text editor and paste your Anthropic API key where indicated. This is optional but
strongly recommended. Your keys stay in `.env` on your own machine, and git is set up to never commit
that file. Don't share it or paste keys on the command line.

Then start everything:

```bash
docker compose up --build -d        # or: make up
```

| | |
|---|---|
| Dashboard | http://localhost:3000 |
| API docs (OpenAPI) | http://localhost:8000/docs |
| Health | http://localhost:8000/health/ready |

This starts PostgreSQL + pgvector, Redis, the API (which runs database migrations on start), a Celery
worker with Chromium, Celery beat, and the dashboard. Open the dashboard, create your account, and
follow the **Get your agent ready** checklist.

Optional: `docker compose exec api python scripts/seed_db.py` creates a demo account
(`demo@example.com` / `demo-password-123`) with sample data, so every page has something to show.

## Try the whole loop safely with the demo careers site

`scripts/demo_site.py` is a fake company, "Acme Robotics". It has a careers page with three job postings
and real application forms that record submissions locally. Use it to watch the complete loop before
pointing the agent at real employers.

```bash
./start.sh --demo                               # starts it on :8765 along with everything else
python3 scripts/demo_site.py --host 0.0.0.0     # or on its own (no dependencies)
```

1. In the dashboard, upload your resume in **Resume Lab** (PDF, DOCX or TXT), or paste it as text.
2. In **Settings → Saved answers**, save your work authorization and sponsorship answers.
3. In **Settings → Preferences**, set a target role such as `Software Engineer`.
4. In **Settings → Job sources**, add a careers page and enable **Career pages** under platforms:
   - Docker: `http://host.docker.internal:8765/careers`
   - Local (non-Docker) setup: `http://127.0.0.1:8765/careers`
5. Click **Scan for jobs now**. The postings appear in **Swipe Review**.
6. Keep one (drag right or press →). It's tailored, filled in Chromium and submitted. Turn off
   **Apply automatically** first if you'd rather check the form screenshot, resume, cover letter and
   answers and click **Review & approve** yourself.
7. Open http://localhost:8765/submissions to see exactly what was submitted, including your resume PDF.

## Using it for real

1. **Upload your master resume** in Resume Lab and check the parsed result. Everything the agent writes
   is derived from it, so make it complete and accurate.
2. **Saved answers** (Settings): fill in work authorization, sponsorship, notice period, salary
   expectation, address, EEO preferences and so on. These answer most form questions directly.
3. **Mass apply** (Settings): apply the **AI + Python · 2nd year** preset, then check
   **Preferences**: target roles and locations, remote preference, salary range, job types, companies
   to target or avoid, excluded keywords and the daily application limit.
4. **Job sources**: add the companies you care about:
   - Greenhouse board tokens (`stripe` from `job-boards.greenhouse.io/stripe`)
   - Lever slugs (`jobs.lever.co/<company>`)
   - Ashby boards (`jobs.ashbyhq.com/<board>`)
   - Workday site URLs (`https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite`)
   - any careers page URL

   LinkedIn, Indeed, Glassdoor and Wellfound searches use your target roles and locations.
5. Optionally **connect Google** (Gmail + Calendar) and **sync LinkedIn** (and Internshala) with the
   Chrome extension.
6. Start with `SUBMISSION_DRY_RUN=true` for a day. You get everything except the final click. Then
   turn it off.

Use **Scan for jobs now**, or let the scheduler scan every `scan_interval_hours`. New jobs land in
**Swipe Review**; kept jobs are applied to, and anything that needs you arrives in **Needs approval**
with a notification.

## Connect Gmail and Google Calendar

1. In the [Google Cloud console](https://console.cloud.google.com/), create a project and enable the
   **Gmail API** and the **Google Calendar API**.
2. **OAuth consent screen**: choose External, add your e-mail as a test user, and add the scopes
   `gmail.readonly`, `gmail.modify`, `gmail.labels`, `calendar.events` and `calendar.readonly`.
   > While the app's publishing status is **Testing**, Google expires refresh tokens after 7 days and
   > you'll have to reconnect weekly. For personal use you can set the status to **In production**
   > without verification. You'll see an "unverified app" warning when connecting, and tokens stop
   > expiring.
3. **Credentials → Create credentials → OAuth client ID**: choose type **Web application**, and add
   this authorized redirect URI:
   ```
   {FRONTEND_URL}/api/v1/auth/google/callback      e.g. http://localhost:3000/api/v1/auth/google/callback
   ```
4. Paste the client ID and client secret into their places in your `.env` file. Keep the secret
   private and never commit it. Restart (`docker compose up -d`), then go to
   **Settings → Google → Connect**.

**Optional: real-time Gmail push** (otherwise the inbox is polled every `EMAIL_POLL_MINUTES`). This
needs a public HTTPS URL.

1. Create a Pub/Sub topic.
2. Grant `gmail-api-push@system.gserviceaccount.com` the **Pub/Sub Publisher** role on the topic.
3. Create a push subscription to `https://<your-domain>/api/v1/webhooks/gmail?token=<GMAIL_PUBSUB_VERIFICATION_TOKEN>`.
4. Set `GMAIL_PUBSUB_TOPIC=projects/<project>/topics/<topic>` and `GMAIL_PUBSUB_VERIFICATION_TOKEN`.

The watch is renewed daily.

## LinkedIn, Internshala and the Chrome extension

LinkedIn search and Easy Apply run under your own LinkedIn session, and the optional Internshala bot
under your own Internshala login. The extension in [`extension/`](extension) ("AutoApply AI — Session
Sync") copies those sessions to your AutoApply server. They are stored encrypted and re-synced every
12 hours and whenever LinkedIn or Internshala renews them.

1. Open `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and select the
   `extension/` folder. CI also builds a zip artifact of it. **Updating from 1.0?** Click the reload
   icon on the extension card (or load the folder again): version 1.1 asks for access to
   internshala.com.
2. In the dashboard, go to **Settings → Integrations → Generate extension token**.
3. Open the extension popup and enter your dashboard URL (for example `http://localhost:3000`) and the
   token. Allow access to that origin when Chrome asks, then click **Sync LinkedIn session** while
   logged in to LinkedIn.

The extension reads the LinkedIn `li_at` cookie and, once you click **Sync Internshala session**, your
internshala.com cookies (including the httpOnly login cookies, which a web page can't read). Nothing
is sent anywhere except the dashboard URL you entered, and the dashboard never shows the cookie values.

### The Internshala bot (opt-in)

Internshala only takes applications from your own logged-in account, and logging in from a script hits
a reCAPTCHA, so the bot borrows the login of your real Chrome instead.

1. Log into [Internshala](https://internshala.com) in Chrome and click **Sync Internshala session** in
   the extension popup. **Settings → Integrations → Internshala** shows the login as synced; **Check
   session** opens Internshala with it to confirm it still works.
2. Turn on **Let the agent apply on Internshala** and read the warning (below) before you confirm.
3. Keep Internshala jobs in Swipe Review as usual. The agent opens each one in a real browser, clicks
   **Apply now** (through the "Proceed to application" step when Internshala shows it) and fills the
   form: the cover letter ("Why should you be hired for this role?", as plain text without a
   salutation), your availability (kept at "available immediately" unless your saved *notice period*
   answer says otherwise), the relocation box (from your saved *willing to relocate* answer) and every
   assessment question, using your saved answers first. Your **Internshala profile resume** is what
   Internshala attaches; the agent never replaces it. Nothing is sent: it takes a screenshot and the
   application waits for you.
4. Review it (the filled fields, answers and screenshot) and click **Submit**. Only then does the
   agent open the form again, fill it the same way and click Internshala's Submit button; the
   confirmation screenshot is saved with the application.
5. Optional: **Submit automatically** sends Internshala jobs you keep as soon as they're filled.
   Anything the agent is unsure about (a required question it couldn't answer, a low-confidence
   eligibility answer) still waits for you.

Limits: at most **Daily limit** Internshala applications a day (15 by default, 25 at most), 60–180
seconds apart. Approved applications over the limit wait and go out the next day.

What happens when an internship can't be applied to here:

- **External listings** ("You will be redirected to another website"): nothing is submitted; the
  application shows the company's own link so you can apply there.
- **Already applied** on Internshala: the application is marked **Applied** and tracked, not failed.
- **Applications closed**, or Internshala asks you to **complete your profile** first: the application
  stays in your review queue with the reason.
- **Session expired**: you get a notification ("open Internshala in Chrome and click Sync in the
  extension"), Submit is paused until you re-sync, and the application stays in your queue.

> **Account risk.** Internshala's terms don't allow bots or automated access without its consent, and
> Internshala can restrict or suspend accounts it believes are automated. The bot is off by default,
> applies slowly and only to internships you kept, and you review every application unless you turn on
> Submit automatically. You use it at your own risk. Internshala also changes its pages from time to
> time; every selector the bot uses is in one place, `SELECTORS` in
> [`backend/app/submitters/internshala_apply.py`](backend/app/submitters/internshala_apply.py).

## Local development without Docker

`./start.sh` does all of the following for you; the individual steps are here if you prefer them.

Requirements: Python 3.11+, Node 20+, PostgreSQL 16 with the `pgvector` extension (or SQLite for a
quick try), and Redis (optional with `CELERY_TASK_ALWAYS_EAGER=true`).

```bash
make setup          # backend venv + deps + Chromium, dashboard deps, .env
make migrate        # alembic upgrade head (or create tables on SQLite)
make api            # FastAPI on :8000 (reload)
make worker         # Celery worker        ┐ or set CELERY_TASK_ALWAYS_EAGER=true
make beat           # Celery beat schedule ┘ and skip both
make web            # Next.js dev server on :3000
make demo           # demo careers site on :8765
```

Simplest possible setup, with no Postgres or Redis: set `DATABASE_URL=sqlite:///./data/autoapply.db`,
`CELERY_TASK_ALWAYS_EAGER=true` and `REDIS_URL=` in `.env`, then run `make api` and `make web`.
Scheduled scans need beat and Redis.

Other useful commands:

```bash
make help                                                   # everything available
backend/.venv/bin/python scripts/test_scraper.py greenhouse --source stripe -k "Software Engineer"
backend/.venv/bin/python scripts/test_scraper.py url https://job-boards.greenhouse.io/stripe/jobs/123
backend/.venv/bin/python scripts/migrate.py --reembed       # after changing EMBEDDING_PROVIDER
```

## Deployment

### Free, always on: Oracle Cloud "Always Free"

Oracle's Always Free tier includes an ARM server with up to 4 cores and 24 GB of RAM that doesn't
expire. That's enough to run everything 24/7 at no cost.

1. **Create an account** at https://www.oracle.com/cloud/free/. A card is needed for verification
   but isn't charged. Your *home region* can't be changed later, so pick one near you.
2. **Create the server.** Go to **Compute → Instances → Create instance** and set:
   - **Image**: Canonical Ubuntu 24.04.
   - **Shape**: *Change shape → Ampere → VM.Standard.A1.Flex*, 4 OCPUs and 24 GB memory. It's
     labelled "Always Free-eligible".
   - **Networking**: keep "Create new virtual cloud network" and "Assign a public IPv4 address".
   - **SSH keys**: *Generate a key pair* and **download the private key**.

   If you see "Out of capacity", try another availability domain, try 2 OCPUs / 12 GB, or retry
   later. This is common for free ARM servers.
3. **Open ports 80 and 443.** On the instance page, click the subnet, then its **Security List**,
   then **Add Ingress Rules**. Set source CIDR `0.0.0.0/0`, IP protocol TCP, and destination port
   range `80,443`.
4. **Connect and run the setup script.** The instance page shows the public IP address.
   ```bash
   chmod 600 ~/Downloads/ssh-key-*.key
   ssh -i ~/Downloads/ssh-key-*.key ubuntu@<PUBLIC_IP>
   curl -fsSL https://raw.githubusercontent.com/saksham-eng560/autoapply-ai/main/scripts/server-setup.sh | bash
   ```
   [`scripts/server-setup.sh`](scripts/server-setup.sh) does the rest:
   - installs Docker and opens the server's own firewall (Oracle's Ubuntu image blocks everything
     except SSH);
   - generates fresh secrets into `~/autoapply-ai/.env`;
   - builds and starts the stack with HTTPS. The first build takes about 10 minutes.

   It prints your URL when it's done. Without a `DOMAIN`, the URL is
   `https://<ip-with-dashes>.sslip.io`, a free hostname that points at your server.
5. **Add your API key privately.** On the server, run `nano ~/autoapply-ai/.env`, paste your
   Anthropic key into its line, and save. Then restart:
   `cd ~/autoapply-ai && docker compose -f docker-compose.prod.yml up -d`. Editing the file keeps the
   key out of your shell history and out of git. The file is readable only by your user.
6. **Open the URL and create your account.** Then turn off sign-ups as the script's output shows.

Useful follow-ups:
- **Your own domain**: point its DNS A record at the server, then re-run the script with
  `DOMAIN=jobs.example.com` in front of `bash`. A real domain is recommended before connecting
  Google.
- **Changing settings**: edit `~/autoapply-ai/.env`, then run
  `docker compose -f docker-compose.prod.yml up -d` in that folder.
- **Updating**: re-run the script.
- **Free AI on the server**: re-run the script with `WITH_OLLAMA=1` in front of `bash`. See
  [Free AI with Ollama](#free-ai-with-ollama).
- **Idle servers**: Oracle may reclaim Always Free servers that stay almost completely idle for a
  week. Scheduled scans normally keep the server active enough. Upgrading the account to
  Pay-As-You-Go removes this risk, and Always Free resources stay free.

### Any other server with automatic HTTPS

This works on any Ubuntu VPS with 2+ vCPU and 4+ GB RAM (Hetzner, DigitalOcean, Lightsail…). Either
run the same `server-setup.sh` command as above, or do it by hand:

```bash
# DNS: point your domain at the server, then:
scripts/init-env.sh      # private .env with generated secrets; then add your domain and API keys with an editor
docker compose -f docker-compose.prod.yml up -d --build      # or: make prod-up
```

Caddy obtains a Let's Encrypt certificate for `DOMAIN` automatically. It routes `/api/v1/*`, `/docs`
and `/health*` to the API and everything else to the dashboard. Set `ALLOW_REGISTRATION=false` once
your account exists. Set the Google redirect URI to `https://<DOMAIN>/api/v1/auth/google/callback`.
Back up the `pgdata` and `storage` volumes.

### AWS ECS Fargate + Vercel (the plan's production architecture)

- **Backend**: [`deploy-backend.yml`](.github/workflows/deploy-backend.yml) builds the backend image,
  pushes it to ECR, and rolls out new task definitions for the API, worker and beat ECS services.
  - It runs after CI passes on `main`, or manually.
  - Setup: create RDS PostgreSQL 16 (run `CREATE EXTENSION vector;`), ElastiCache Redis, an S3 or R2
    bucket, an ECR repository `autoapply-backend`, and three ECS services running the same image with
    commands `api`, `worker` and `beat`.
  - Suggested sizing: API 2 vCPU / 4 GB behind an ALB, worker 4 vCPU / 8 GB, beat 0.25 vCPU / 0.5 GB,
    with exactly **one** beat task.
  - Repository variables: `AWS_REGION`, `AWS_ROLE_ARN` (a GitHub OIDC role), `ECS_CLUSTER`, and
    optionally `ECR_REPOSITORY` and `ECS_SERVICES`.
  - Migrations run when the API starts, serialized with a Postgres advisory lock so replicas can start
    together.
- **Dashboard**: [`deploy-frontend.yml`](.github/workflows/deploy-frontend.yml) deploys to Vercel.
  - Create a Vercel project with Root Directory `frontend`.
  - Set its environment variables: `BACKEND_URL=https://api.<domain>` and
    `NEXT_PUBLIC_WS_URL=wss://api.<domain>/api/v1/ws`.
  - Add repository variables `VERCEL_ORG_ID` and `VERCEL_PROJECT_ID`, and the secret `VERCEL_TOKEN`.
  - Set `FRONTEND_URL` on the backend to the Vercel URL.

Both workflows skip themselves until their variables are set.

## Testing and CI

```bash
make test           # 90 backend tests on SQLite, incl. real-Chromium end-to-end tests (swipe → submit)
make test-pg        # the same suite on PostgreSQL + pgvector (TEST_DATABASE_URL)
make e2e            # only the browser end-to-end test
make lint           # ruff (backend + scripts), ESLint + TypeScript (dashboard)
```

The end-to-end test starts a mock company site with a careers page and an ATS form, then runs the real
pipeline: scan, match, tailor, fill in Chromium, pause, approve, submit. It asserts the exact fields the
employer received, and that nothing was sent before approval.

[`ci.yml`](.github/workflows/ci.yml) runs on every push and PR:

- **Backend**: ruff, bandit, a migration round trip (`upgrade`, then `check` for drift, then
  `downgrade`, then `upgrade`), and the test suite on both SQLite and PostgreSQL with coverage.
- **Dashboard**: lint, typecheck and production build.
- **Extension**: validation and packaging.
- **Docker**: builds both images, then smoke-tests the backend image by launching Chromium.

## Project structure

```
backend/
  app/
    api/            FastAPI routers (auth, users, resumes, jobs, applications, agent, communications,
                    interviews, analytics, files/webhooks/WebSocket)
    automation/     Playwright browser sessions, stealth, human emulation, proxies, CAPTCHA
    core/           database, security (JWT, bcrypt, AES-GCM), storage, Redis, WebSocket hub, logging
    models/         SQLAlchemy models (users, resumes, jobs, applications, communications, interviews, runs)
    schemas/        Pydantic request/response and resume-content schemas
    scrapers/       Greenhouse, Lever, Ashby, Workday, LinkedIn, Indeed, Glassdoor, Wellfound, curated
                    internship lists (SimplifyJobs, vanshb03), generic careers pages
    services/       orchestrator, LLM client, matcher, tailor + truthfulness guard, cover letters, question
                    answerer, resume parser, PDF generator, Gmail, e-mail parser, calendar, notifier,
                    analytics, rate limiter, LinkedIn sync, privacy
      agent/        the agent's pipeline, one module per stage: runs, jobs, discovery, scoring, Swipe Review,
                    preparation, submission, the Internshala bot, the scan (agent_orchestrator.py is the entry point)
    submitters/     Greenhouse, Lever, Workday, LinkedIn Easy Apply, Internshala (opt-in), generic form engine
    worker/         Celery app, beat schedule and tasks
  alembic/          migrations (pgvector, enums, indexes)
  tests/            unit, API, scraper-fixture and browser end-to-end tests
frontend/           Next.js 14 dashboard (app router, Tailwind, shadcn/ui + Radix, framer-motion, SWR, Recharts, PWA)
extension/          Chrome MV3 extension (LinkedIn and Internshala session sync)
prompts/            all LLM prompts as editable text files
scripts/            demo careers site, migrations, seed data, scraper CLI, local scheduler
start.sh, start.bat one-command launchers
docs/screenshots/   dashboard screenshots
docker-compose.yml, docker-compose.prod.yml, Caddyfile, Makefile, .github/workflows/
PLAN.md             the full design this implementation follows
```

## Security and privacy

- **Passwords**: bcrypt.
- **Sessions**: httpOnly SameSite cookies, with `COOKIE_SECURE` in production. Short-lived scoped
  tokens for the WebSocket and the extension.
- **Credentials**: OAuth refresh tokens and the LinkedIn and Internshala sessions are encrypted at rest
  (AES-256-GCM), and never returned by the API or included in your data export.
- **Isolation**: every query is scoped to the signed-in user, and file downloads are checked against
  the owner.
- **LLM prompts** never include passwords or tokens. Resume content is sent to the configured model
  provider only.
- **Your data**: **Settings → Export your data** gives you everything as JSON. **Delete my account**
  removes the database rows, stored files and tokens. Closed applications are purged after `DATA_RETENTION_DAYS`.
- **Rate limits**: per-platform application limits and randomized pacing protect your accounts.
- **Secrets**: all keys live only in `.env` on your machine or server. That file is created with
  permissions for your user only and is excluded from git. Never paste keys into the README, issues,
  chat or the command line. If a key is ever exposed, revoke it at the provider and put a new one
  in `.env`.
- **Production**: the setup scripts generate strong secrets and enable HTTPS and secure cookies.
  Turn off sign-ups once your own account exists.

## Responsible use

AutoApply AI is a personal tool. It applies **as you**, with **your real information**, to jobs **you
approved**.

- **Truthfulness is enforced.** The agent can reword and reorder your experience, but it cannot invent
  it. Review every application anyway: you are the one submitting it.
- **Respect site terms.** Some job sites, notably LinkedIn, Internshala, Indeed and Glassdoor, restrict
  automated access in their terms of service, and automated use can get an account restricted. The
  Internshala bot is off until you turn it on.
  - The public ATS APIs (Greenhouse, Lever, Ashby, Workday) and company careers pages are the most
    reliable sources.
  - Use browser-based sources sparingly and keep the default rate limits.
  - You are responsible for how you use this tool.
- **Volume with care.** Mass applying works best when you keep the jobs you'd genuinely take, keep
  your saved answers accurate, and stay within the default rate limits.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Invalid email or password" on a Mac / local setup | Each copy of the project folder has its own database (`backend/data/autoapply.db`), so the account may be in another copy. Run `backend/.venv/bin/python scripts/account.py where you@example.com` to see which database the app uses, which accounts it holds and where else your account is. Forgot the password? `backend/.venv/bin/python scripts/account.py reset-password you@example.com` (typed at a hidden prompt). `./start.sh` also prints how many accounts its database holds. |
| Dashboard shows "degraded" at `/api/health` | The API isn't reachable from the dashboard. Check `docker compose logs api` and that `BACKEND_URL` points to it. |
| Nothing gets prepared after a scan | In swipe mode nothing is prepared until you keep it: open **Swipe Review**. Upload a master resume first. **All jobs** shows each job's score and why it was skipped, and **Agent Logs** shows each run step by step. |
| Kept jobs stop in "Needs approval" | A question needs you (usually visa sponsorship or work authorization). Save the answer in **Settings › Saved answers** once and future forms are filled automatically. |
| `./start.sh` says a port is in use | Something else runs on :3000 or :8000. Stop it, or run `API_PORT=8010 WEB_PORT=3010 ./start.sh`. |
| "Required answer(s) are empty" at approval | Eligibility questions are never guessed. Answer them once and they're remembered (also editable in **Settings → Saved answers**). |
| Application fails with a CAPTCHA or bot block | Add a CAPTCHA-solver key and residential proxies to `.env`, or use **Mark as applied** after applying manually through the form link. |
| LinkedIn session invalid | Log in to LinkedIn in Chrome and click **Sync LinkedIn session** in the extension. |
| Internshala session expired | Open Internshala in Chrome (log in if needed) and click **Sync Internshala session** in the extension, then **Check session** in Settings › Integrations. |
| Internshala bot says it can't find the Apply button or the form | Internshala changed its pages. Update the selectors in `SELECTORS` in `backend/app/submitters/internshala_apply.py`; meanwhile apply yourself and click **I Applied**. |
| Google disconnects every 7 days | Your OAuth consent screen is in Testing mode (see [Connect Gmail and Google Calendar](#connect-gmail-and-google-calendar)). |
| Chromium crashes in Docker | Give the worker more shared memory (`shm_size`, 1–2 GB is already set) and RAM. |
| Change the AI's behaviour | Edit the prompt files in `prompts/`, then restart the API and worker. |
| Test AI: "Ollama isn't running" | Open the Ollama app (Mac) or run `ollama serve`. On a server: `COMPOSE_PROFILES=ollama` in `.env`, then `docker compose -f docker-compose.prod.yml up -d`. Check `OLLAMA_BASE_URL`. |
| Test AI: "model isn't downloaded" | Press **Download model** in Settings › Integrations, or run `ollama pull <model>` where Ollama runs. |
| Ollama answers are cut off or slow | Raise `OLLAMA_NUM_CTX` (e.g. 16384) or `OLLAMA_TIMEOUT_SECONDS`, or pick a smaller model such as `llama3.2:3b`. |

## License

[MIT](LICENSE)
