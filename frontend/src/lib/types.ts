export type ApplicationStatus =
  | "discovered" | "matched" | "skipped" | "preparing" | "pending_approval" | "approved" | "applied"
  | "acknowledged" | "screening" | "interview" | "assessment" | "final_round" | "offer" | "accepted"
  | "rejected" | "withdrawn" | "failed";

export interface Preferences {
  target_roles: string[];
  target_locations: string[];
  remote_preference: "remote" | "hybrid" | "onsite" | "any";
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string;
  experience_level: string[];
  industries: string[];
  company_size_preference: string[];
  companies_to_avoid: string[];
  companies_to_target: string[];
  max_applications_per_day: number;
  auto_apply_threshold: number;
  job_types: string[];
  notification_channels: string[];
  keywords_exclude: string[];
  posted_within_days: number;
  scan_enabled: boolean;
  scan_interval_hours: number;
  platforms: string[];
  sources: {
    greenhouse_boards: string[];
    lever_companies: string[];
    ashby_boards: string[];
    workday_sites: string[];
    career_pages: string[];
    internship_lists?: string[];
    internshala_urls?: string[];
  };
  location_focus?: LocationFocus;
  internship_season?: string | null;
  progress_updates_everywhere?: boolean;
  progress_digest?: "daily" | "weekly" | "off";
  /** false: notifications only collect in the bell, with no pop-ups in the dashboard or the OS. */
  notification_popups?: boolean;
  /** At most this % of each scan's new postings come from Internshala (default 25). */
  internshala_share?: number;
  /** And never more than this many new Internshala postings a scan (default 10). */
  internshala_per_scan?: number;
  /** No job site fills more than this % of a scan's new postings while the others have some (default 40). */
  source_share?: number;
  /** Fewer cards waiting in Swipe Review: near misses of your tech focus / length / good-companies-only fill up (0 = off). */
  fill_deck_to?: number;
  /** Intern roles only, at every company (default on): no full-time, new-grad or contract jobs. */
  internships_only?: boolean;
  /** 1–5, null = any year: internships only for other years (final-year only, PhD / MBA only...) are skipped. */
  year_of_study?: number | null;
  /** null = read from your resume (else estimated from your year of study). */
  graduation_year?: number | null;
  /** e.g. 2: internships whose posting states a longer duration are skipped. null = any length. */
  max_internship_months?: number | null;
  /** Your tech focus (AI, LLMs, Python, FastAPI...): a posting must name at least one. Empty = no filter. */
  focus_skills?: string[];
  /** Technologies you skip (Java, Spring Boot...): roles in them are left out unless they also ask for your stack. */
  avoid_skills?: string[];
  skip_suspicious_companies?: boolean;
  /** Only renowned companies, startups on their own job board and companies you marked legit. */
  verified_companies_only?: boolean;
  /** Companies you marked legit: applied to automatically like verified ones. */
  trusted_companies?: string[];
  /** Every scan also searches big tech, product companies, top startups and AI companies. */
  scan_top_companies?: boolean;
  review_mode: "swipe" | "auto";
  auto_submit_kept: boolean;
  trust_generated_answers: boolean;
  resume_strategy: "original" | "light" | "full";
  auto_keep_min_score: number | null;
  max_jobs_per_source: number | null;
  exclude_no_sponsorship: boolean;
  /** Opt-in Internshala apply bot (uses your Internshala login synced by the extension). */
  internshala_bot_enabled?: boolean;
  /** Send Internshala applications without your click (off: they wait in your review queue). */
  internshala_auto_submit?: boolean;
  /** At most this many Internshala applications a day (1–25). */
  internshala_daily_limit?: number;
  discord_webhook_url: string | null;
  slack_webhook_url: string | null;
  timezone: string;
  cover_letter_enabled: boolean;
  resume_template?: string;
  auto_draft_replies?: boolean;
}

export interface LocationFocus {
  enabled?: boolean;
  country: string;
  prime_cities: string[];
  country_share: number;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  phone: string | null;
  location: string | null;
  linkedin_url: string | null;
  has_password: boolean;
  google_connected: boolean;
  google_email: string | null;
  linkedin_connected: boolean;
  linkedin_session_valid: boolean;
  preferences: Preferences;
  last_scan_at: string | null;
  created_at: string;
}

export interface JobApplicationRef {
  id: string;
  status: ApplicationStatus;
  match_score: number | null;
  match_reasoning: string | null;
  similarity_score: number | null;
}

export interface Job {
  id: string;
  company_name: string;
  company_logo_url: string | null;
  role_title: string;
  location: string | null;
  is_remote: boolean;
  job_type: string | null;
  experience_level: string | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  source_url: string;
  source_platform: string;
  application_url: string | null;
  easy_apply: boolean;
  extracted_skills: string[];
  posted_date: string | null;
  deadline_date: string | null;
  discovered_at: string;
  is_active: boolean;
  description?: string;
  application?: JobApplicationRef;
  /** The company-check agent: only verified companies are applied to automatically. */
  company?: CompanyCheck;
  /** "Open to 2nd-year students" when the posting names your year of study. */
  year_fit?: string | null;
}

/** Your year of study and graduation year as the intern-level filter sees them. */
export interface StudentInfo {
  internships_only: boolean;
  year_of_study: number | null;
  graduation_year: number | null;
  graduation_year_source: "you" | "resume" | "estimate" | "";
}

export type CompanyVerdict = "verified" | "unverified" | "suspicious";
export type CompanyTier = "big_tech" | "product" | "startup_india" | "startup_global" | "ai";

export interface CompanyCheck {
  verdict: CompanyVerdict | null;
  score: number | null;
  reasons: string[];
  /** rules · ai · you (you marked it legit) */
  method: string | null;
  tier: CompanyTier | null;
  tier_label: string | null;
}

export interface TopCompanies {
  tiers: { key: CompanyTier; label: string; count: number }[];
  total: number;
  items: Job[];
  catalog: Record<CompanyTier, string[]>;
  scan_top_companies: boolean;
}

export interface CustomAnswer {
  question: string;
  answer: string;
  field_type?: string | null;
  confidence?: number | null;
  needs_user_review?: boolean | null;
  options?: string[] | null;
  required?: boolean | null;
  source?: string | null;
  field_id?: string | null;
}

export interface FormFieldReport {
  label: string;
  kind: string;
  type: string;
  value: string;
  required: boolean;
  status: "filled" | "skipped" | "unmapped";
  options?: string[];
  confidence?: number;
  needs_user_review?: boolean;
}

export interface HistoryEntry {
  id: string;
  old_status: ApplicationStatus | null;
  new_status: ApplicationStatus;
  changed_by: string;
  notes: string | null;
  created_at: string;
}

export interface ResumeContent {
  personal_info: { name: string; email: string; phone: string; location: string; linkedin: string; github: string; portfolio: string };
  summary: string;
  education: { institution: string; degree: string; field: string; gpa: string; start_date: string; end_date: string; highlights: string[] }[];
  experience: { company: string; title: string; start_date: string; end_date: string; location: string; bullets: string[] }[];
  projects: { name: string; description: string; technologies: string[]; url: string }[];
  skills: { technical: string[]; languages: string[]; tools: string[]; soft_skills: string[] };
  certifications: { name: string; issuer: string; date: string }[];
  awards: string[];
}

export interface Resume {
  id: string;
  label: string | null;
  original_filename: string | null;
  is_master: boolean;
  version: number;
  parent_resume_id: string | null;
  tailored_for_job_id: string | null;
  changes_made: string[];
  pdf_url: string | null;
  original_file_url: string | null;
  created_at: string;
  updated_at: string;
  parsed_content?: ResumeContent;
  parse_method?: string;
}

export interface ApplicationSummary {
  id: string;
  status: ApplicationStatus;
  match_score: number | null;
  match_reasoning: string | null;
  ats_platform: string | null;
  needs_manual_review: boolean;
  manual_review_reason: string | null;
  created_at: string;
  updated_at: string;
  submitted_at: string | null;
  job: Job | null;
  /** You clicked "I Applied" (applied on your own) rather than the agent submitting it. */
  self_applied?: boolean;
}

export interface Communication {
  id: string;
  application_id: string | null;
  direction: "inbound" | "outbound";
  sender_email: string | null;
  sender_name: string | null;
  subject: string | null;
  detected_intent: string | null;
  intent_confidence: number | null;
  urgency: string | null;
  is_action_required: boolean;
  action_taken: boolean;
  received_at: string | null;
  snippet: string;
  body_text?: string | null;
  extracted_details?: Record<string, string | number> | null;
  suggested_reply?: string | null;
  gmail_thread_id?: string | null;
  gmail_draft_id?: string | null;
  application?: { id: string; company_name: string; role_title: string; status: ApplicationStatus } | null;
}

export interface Interview {
  id: string;
  application_id: string;
  company_name: string | null;
  role_title: string | null;
  interview_type: string | null;
  scheduled_at: string;
  duration_minutes: number;
  timezone: string;
  meeting_link: string | null;
  meeting_platform: string | null;
  physical_location: string | null;
  interviewer_names: string[];
  outcome: string | null;
  google_event_id: string | null;
  google_event_link: string | null;
  prep_notes?: string | null;
  company_research?: string | null;
  likely_questions?: { question: string; answer_outline: string }[];
  feedback?: string | null;
}

export interface ApplicationDetail extends ApplicationSummary {
  job: Job | null;
  match_details: Record<string, unknown> | null;
  similarity_score: number | null;
  cover_letter: string | null;
  custom_answers: CustomAnswer[];
  /** Your corrections from "Ready to submit": profile key ("email") or "label:<form label>" -> value. */
  field_overrides?: Record<string, string>;
  form_fields: FormFieldReport[];
  tailored_resume: Resume | null;
  tailored_resume_pdf_url: string | null;
  form_screenshot_url: string | null;
  confirmation_screenshot_url: string | null;
  confirmation_number: string | null;
  staged_at: string | null;
  approved_at: string | null;
  rejection_reason: string | null;
  offer_details: Record<string, unknown> | null;
  error_log: string | null;
  retry_count: number;
  notes: string | null;
  history: HistoryEntry[];
  communications: Communication[];
  interviews: Interview[];
}

/** One prefilled item on the "Ready to submit" sheet. */
export interface ReviewRow {
  /** Profile key ("email"), "label:<form label>" for questions, "cover_letter", "resume" or "file:<label>". */
  key: string;
  label: string;
  kind: "profile" | "question" | "cover_letter" | "resume" | "file";
  value: string;
  /** The form control: text, textarea, select, radio, checkbox, email, tel, number, file... */
  type: string;
  options: string[];
  required: boolean;
  /** The agent filled this in on the staged form. */
  filled: boolean;
  /** Low confidence, flagged for review, or a required field the agent couldn't fill. */
  flagged: boolean;
  source: string;
  confidence: number | null;
  /** Why it's flagged, in plain words. */
  note: string | null;
  /** The resume PDF, for the resume row. */
  url: string | null;
}

export interface SubmitQueueItem extends ApplicationSummary {
  form_screenshot_url: string | null;
  tailored_resume_pdf_url: string | null;
  staged_at: string | null;
  /** Why the agent can't submit this one itself (e.g. an Internshala login): apply there, then "I Applied". */
  blocker: string | null;
  /** Internshala postings: what the apply bot still needs (null when it can fill and submit this itself). */
  bot: { site: string; missing: BotMissing | null } | null;
  apply_url: string | null;
  /** Rows needing your attention first, then the rest in form order. */
  rows: ReviewRow[];
  attention: number;
}

/** Bot switched off in Settings, login never synced from the extension, or the synced login expired. */
export type BotMissing = "bot_off" | "not_synced" | "expired";

export interface SubmitQueue {
  items: SubmitQueueItem[];
  total: number;
}

export interface DirectSubmitResponse extends ApplicationDetail {
  /** The next application waiting in the queue, or null when it was the last one. */
  next_id: string | null;
}

export interface AgentRun {
  id: string;
  run_type: string;
  status: string;
  trigger: string | null;
  jobs_discovered: number;
  jobs_matched: number;
  applications_prepared: number;
  applications_submitted: number;
  errors_count: number;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  progress?: ScanProgress | null;
  log?: { ts: string; level: string; message: string; data?: Record<string, unknown> }[];
}

export type ScanPhase = "discovering" | "saving" | "scoring" | "finishing" | "done" | "cancelled" | "failed";

export interface ScanSourceProgress {
  name: string;
  status: "pending" | "running" | "done" | "failed" | "timeout";
  found: number;
  done: number;
  total: number;
  error?: string;
}

/** Live progress of a scan (agent_runs.progress), pushed over the WebSocket as `scan_progress`. */
export interface ScanProgress {
  phase: ScanPhase;
  percent: number;
  message: string;
  sources: ScanSourceProgress[];
  found: number;
  new: number;
  scored: number;
  to_score: number;
  eta_seconds: number | null;
  /** Jobs sent to the AI for scoring, and how many it has answered (the slow part of a scan). */
  llm_total?: number;
  llm_done?: number;
  updated_at: string;
}

export interface AgentStatus {
  has_master_resume: boolean;
  pending_approval: number;
  preparing: number;
  approved: number;
  failed: number;
  applied_today: number;
  daily_limit: number;
  running_runs: AgentRun[];
  last_scan_at: string | null;
  next_scan_at: string | null;
  scan_enabled: boolean;
  google_connected: boolean;
  linkedin_connected: boolean;
  to_review: number;
  review_mode: "swipe" | "auto";
}

export interface ReviewCard {
  application_id: string;
  status: ApplicationStatus;
  match_score: number | null;
  match_reasoning: string | null;
  strong_matches: string[];
  missing_skills: string[];
  heads_up: string[];
  scores: Partial<Record<"skills_match" | "experience_match" | "industry_match" | "location_match" | "compensation_match", number>>;
  job: Job & { description: string; sponsorship: string | null; terms: string[]; listing_source: string | null };
  discovered_at: string | null;
  /** 0 = prime city (e.g. Delhi NCR), 1 = rest of the focus country, 2 = remote / unknown, 3 = abroad. */
  focus?: { location_tier: number | null; season: string | null; season_label: string | null; country: string | null };
}

export interface ReviewStats {
  remaining: number;
  kept_today: number;
  skipped_today: number;
  kept_total: number;
}

export interface ReviewQueue {
  items: ReviewCard[];
  matching: number;
  stats: ReviewStats;
  settings: { auto_submit_kept: boolean; review_mode: "swipe" | "auto" };
  has_master_resume: boolean;
}

export interface Overview {
  totals: Record<string, number>;
  by_status: Record<string, number>;
  rates: { response_rate: number; interview_rate: number; offer_rate: number; avg_days_to_response: number | null };
  timeline: { date: string; discovered: number; applied: number; responses: number }[];
  match_distribution: { range: string; count: number }[];
  platforms: { platform: string; discovered: number; applied: number; responses: number; interviews: number; response_rate: number; interview_rate: number }[];
  top_keywords: { keyword: string; applications: number; callbacks: number; callback_rate: number; lift: number }[];
  upcoming_interviews: { id: string; company: string; role: string; scheduled_at: string; type: string | null }[];
  recent_runs: { id: string; run_type: string; status: string; started_at: string; jobs_discovered: number; jobs_matched: number; errors_count: number }[];
}

export interface NotificationItem {
  id: string;
  event_type: string;
  title: string;
  body: string | null;
  link: string | null;
  data: Record<string, unknown>;
  is_read: boolean;
  created_at: string;
}

export interface Integrations {
  google: { configured: boolean; connected: boolean; email: string | null; gmail: boolean; calendar: boolean; last_polled_at: string | null; push_enabled: boolean };
  linkedin: { connected: boolean; session_valid: boolean; updated_at: string | null; profile_diff: { has_changes: boolean; changes: { section: string; change: string; linkedin_value: string }[]; summary: string } | null; synced_at: string | null };
  /** The synced Internshala login (never the cookies themselves) and the bot's switches. */
  internshala: { connected: boolean; session_valid: boolean; updated_at: string | null; bot_enabled: boolean; auto_submit: boolean; daily_limit: number };
  llm: {
    /** Provider tried first (null = built-in heuristics only); the rest are fallbacks. */
    provider: string | null;
    providers: string[];
    model: string | null;
    embedding_provider: string;
    ollama: OllamaStatus;
  };
  automation: { proxies: number; captcha: boolean; dry_run: boolean; auto_stage: boolean };
  notifications: { smtp: boolean; discord: boolean; slack: boolean };
  ats_credentials: string[];
}

export interface OllamaPullProgress {
  status: "idle" | "pulling" | "success" | "error";
  model: string | null;
  /** Ollama's own step, e.g. "pulling manifest", "verifying sha256 digest". */
  detail: string | null;
  completed: number;
  total: number;
  percent: number;
  error: string | null;
}

export interface OllamaStatus {
  configured: boolean;
  /** Scheme + host only; keys are never sent to the dashboard. */
  base_url: string;
  cloud: boolean;
  model: string | null;
  reachable: boolean;
  version: string | null;
  /** null when it can't be known (Ollama unreachable, or a cloud model). */
  model_pulled: boolean | null;
  models: string[];
  error: string | null;
  pull: OllamaPullProgress | null;
}

export interface LLMTestResult {
  ok: boolean;
  provider: string | null;
  model: string | null;
  latency_ms: number;
  sample?: string;
  error?: string;
  hint?: string;
}

export interface FieldMapping {
  field_name: string;
  field_value: string;
  field_type: string | null;
  is_secret: boolean;
}

export interface StandardField {
  label: string;
  type: string;
  options?: string[];
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size?: number;
  counts?: Record<string, number>;
  self_applied_total?: number;
}
