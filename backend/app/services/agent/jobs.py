"""Postings and their applications: storing what a scan found (rules #1 and #2), embedding it, the company check's
verdict on it, and the master resume everything is matched against."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import checkpoint
from app.dsa.bloom_filter import BloomFilter
from app.models.application import Application
from app.models.enums import STATUS_RANK, ApplicationStatus, ATSPlatform
from app.models.job import Job
from app.models.resume import Resume
from app.models.user import User
from app.schemas.resume_content import ResumeContent
from app.scrapers import ScrapedJob, detect_ats_platform, fetch_job_from_url
from app.services.agent.runs import RunLog
from app.services.company_verifier import UNVERIFIED, CompanyCheck, check_job, verify_with_llm
from app.services.embeddings import embed_text, embed_texts
from app.services.job_matcher import job_text
from app.services.llm import get_llm
from app.services.scan_progress import ScanProgress
from app.services.text_utils import dedupe_key, extract_skills

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- your resume
def get_master_resume(db: Session, user: User) -> Resume | None:
    return db.scalar(
        select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True), Resume.is_active.is_(True))
        .order_by(Resume.updated_at.desc())
    )


def resume_embedding(db: Session, resume: Resume) -> list[float]:
    if not resume.skills_embedding:
        rc = ResumeContent.model_validate(resume.parsed_content)
        resume.skills_embedding = embed_text(f"{rc.skills_text()}\n{rc.full_text()}")
        db.flush()
    return resume.skills_embedding


# --------------------------------------------------------------------------- postings
class SeenPostings:
    """A Bloom filter over the duplicate key of every active posting, built once per scan.

    ``upsert_job`` asks it before running the "is this the same job from another site?" query. A
    Bloom filter never says "no" wrongly, so "no" lets the query be skipped for a posting that is
    certainly new; "maybe" still runs the exact query, so a false positive costs one query and
    never drops a job. Postings saved by another process while this scan runs are not in the
    filter; the unique index on ``source_url`` still stops the same listing being stored twice.
    """

    def __init__(self, keys: list[str], room: int = 5000) -> None:
        self.filter = BloomFilter(expected_items=len(keys) + room, false_positive_rate=0.01, items=keys)
        self.skipped = 0  # exact queries avoided ("definitely new")
        self.checked = 0  # exact queries still run ("maybe seen")

    @classmethod
    def load(cls, db: Session) -> SeenPostings:
        keys = db.scalars(select(Job.dedupe_key).where(Job.is_active.is_(True), Job.dedupe_key.is_not(None))).all()
        return cls([key for key in keys if key])

    def maybe_seen(self, key: str) -> bool:
        hit = key in self.filter
        if hit:
            self.checked += 1
        else:
            self.skipped += 1
        return hit

    def add(self, key: str) -> None:
        self.filter.add(key)

    def summary(self) -> str:
        stats = self.filter.stats()
        return (f"Bloom filter ({stats['bytes']:,} bytes for {stats['items']:,} postings) answered \"certainly new\" for "
                f"{self.skipped} postings, saving that many duplicate-check queries; {self.checked} were checked exactly")


def upsert_job(db: Session, scraped: ScrapedJob, seen: SeenPostings | None = None) -> tuple[Job, bool]:
    """Insert or refresh a job. Returns (job, created). ``seen`` (a scan's Bloom filter) lets the
    cross-site duplicate query be skipped for postings that are certainly new."""
    job = db.scalar(select(Job).where(Job.source_url == scraped.source_url))
    now = datetime.now(UTC)
    if job is not None:
        job.last_checked = now
        job.is_active = True
        if scraped.description and len(scraped.description) > len(job.description or ""):
            job.description = scraped.description
        if job.company_verdict is None:
            apply_company_check(job)
        return job, False
    key = dedupe_key(scraped.company_name, scraped.role_title, scraped.location)
    if seen is None or seen.maybe_seen(key):
        duplicate = db.scalar(select(Job).where(Job.dedupe_key == key, Job.is_active.is_(True)))
        if duplicate is not None:
            duplicate.last_checked = now
            return duplicate, False
    if seen is not None:
        seen.add(key)
    job = Job(
        company_name=scraped.company_name,
        company_logo_url=scraped.company_logo_url,
        company_domain=scraped.company_domain,
        role_title=scraped.role_title,
        description=scraped.description or scraped.role_title,
        requirements=scraped.requirements,
        job_type=scraped.job_type,
        experience_level=scraped.experience_level,
        location=scraped.location,
        is_remote=scraped.is_remote,
        salary_min=scraped.salary_min,
        salary_max=scraped.salary_max,
        salary_currency=scraped.salary_currency or "USD",
        source_url=scraped.source_url,
        source_platform=scraped.source_platform,
        application_url=scraped.application_url or scraped.source_url,
        external_id=scraped.external_id,
        easy_apply=scraped.easy_apply,
        dedupe_key=key,
        extracted_skills=extract_skills(f"{scraped.role_title}\n{scraped.description}"),
        raw_data=scraped.raw,
        posted_date=scraped.posted_date,
        deadline_date=scraped.deadline_date,
    )
    apply_company_check(job)
    db.add(job)
    db.flush()
    return job, True


def embed_jobs(db: Session, jobs: list[Job]) -> None:
    missing = [j for j in jobs if not j.description_embedding]
    for start in range(0, len(missing), 64):
        batch = missing[start : start + 64]
        vectors = embed_texts([job_text(j)[:8000] for j in batch])
        for job, vec in zip(batch, vectors, strict=True):
            job.description_embedding = vec
    db.flush()


def enrich_job(db: Session, job: Job) -> bool:
    """Postings from curated lists carry only a title; fetch the real description when we can."""
    if not (job.raw_data or {}).get("listing_source") or len(job.description or "") >= 400:
        return False
    try:
        scraped = fetch_job_from_url(job.application_url or job.source_url)
    except Exception as exc:  # noqa: BLE001 - best effort, the application still works without it
        logger.info("Could not enrich job %s: %s", job.id, exc)
        return False
    if scraped is None or len(scraped.description or "") <= len(job.description or ""):
        return False
    job.description = scraped.description
    job.requirements = job.requirements or scraped.requirements
    if scraped.salary_min or scraped.salary_max:
        job.salary_min, job.salary_max = scraped.salary_min, scraped.salary_max
        job.salary_currency = scraped.salary_currency or job.salary_currency
    job.extracted_skills = extract_skills(f"{job.role_title}\n{job.description}")
    job.description_embedding = None
    embed_jobs(db, [job])
    return True


def user_source_urls(db: Session, user: User, contains: str) -> frozenset[str]:
    """Source URLs (containing ``contains``) of the postings you already have."""
    rows = db.scalars(select(Job.source_url).join(Application, Application.job_id == Job.id)
                      .where(Application.user_id == user.id, Job.source_url.contains(contains))).all()
    return frozenset(rows)


def known_source_urls(db: Session, days: int = 30) -> frozenset[str]:
    """Postings from sites with slow detail pages that are already saved (with a full description)."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.scalars(select(Job.source_url).where(
        Job.is_active.is_(True), Job.last_checked >= since, func.length(Job.description) >= 200,
        Job.source_platform.in_([ATSPlatform.LINKEDIN, ATSPlatform.INDEED, ATSPlatform.GLASSDOOR]),
    )).all()
    return frozenset(rows)


# --------------------------------------------------------------------------- applications
def application_platform(job: Job) -> ATSPlatform:
    url = job.application_url or job.source_url
    if job.source_platform == ATSPlatform.LINKEDIN:
        external = (job.raw_data or {}).get("external_apply_url")
        if external:
            return detect_ats_platform(external)
        return ATSPlatform.LINKEDIN if job.easy_apply else detect_ats_platform(url)
    platform = detect_ats_platform(url)
    if platform in (ATSPlatform.INDEED, ATSPlatform.GLASSDOOR, ATSPlatform.WELLFOUND):
        return ATSPlatform.CUSTOM
    return platform


def ensure_application(db: Session, user: User, job: Job) -> tuple[Application, bool]:
    """Rule #1: one application per job and user. Returns (application, created)."""
    app = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    if app is not None:
        return app, False
    app = Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.DISCOVERED,
                      ats_platform=application_platform(job))
    db.add(app)
    db.flush()
    return app, True


def already_applied_elsewhere(db: Session, user: User, job: Job) -> bool:
    """Rule #2: same company + role already in the pipeline for this user."""
    rows = db.execute(
        select(Application.status, Job.id)
        .join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user.id, Job.dedupe_key == job.dedupe_key, Job.id != job.id)
    ).all()
    return any(STATUS_RANK.get(status, 0) >= STATUS_RANK[ApplicationStatus.PREPARING] for status, _ in rows)


# --------------------------------------------------------------------------- the company check
COMPANY_AI_CHECKS_PER_SCAN = 8  # distinct unknown companies the AI looks at per scan (local models are slow)


def apply_company_check(job: Job, check: CompanyCheck | None = None) -> None:
    """Store the company-check agent's verdict on the job (services/company_verifier.py)."""
    check = check or check_job(job)
    job.company_verdict = check.verdict
    job.company_tier = check.tier or (job.raw_data or {}).get("company_tier")
    job.company_check = check.as_dict()


def backfill_company_checks(db: Session, limit: int = 2000) -> int:
    """Postings saved before the company check existed get their verdict (the rules: instant)."""
    jobs = db.scalars(select(Job).where(Job.company_verdict.is_(None)).limit(limit)).all()
    for job in jobs:
        apply_company_check(job)
    if jobs:
        db.flush()  # sessions don't autoflush: later queries in this request must see the verdicts
    return len(jobs)


def verify_companies(db: Session, jobs: list[Job], run: RunLog | None = None, progress: ScanProgress | None = None) -> int:
    """The company-check agent's second look: the AI on companies the rules couldn't verify (cached per
    company: a verdict the AI gave in the last 30 days is reused)."""
    if not get_llm().available:
        return 0
    seen: dict[str, CompanyCheck | None] = {}
    asked = 0
    since = datetime.now(UTC) - timedelta(days=30)
    for job in jobs:
        if job.company_verdict != UNVERIFIED or (job.company_check or {}).get("method") != "rules":
            continue
        key = job.company_name.strip().lower()
        if key not in seen:
            earlier = db.scalars(select(Job).where(func.lower(Job.company_name) == key, Job.id != job.id,
                                                   Job.discovered_at >= since).limit(20)).all()
            done = next((j for j in earlier if (j.company_check or {}).get("method") == "ai"), None)
            if done is not None:
                seen[key] = CompanyCheck(done.company_verdict or UNVERIFIED, int(done.company_check.get("score") or 50),
                                         list(done.company_check.get("reasons") or []), done.company_tier, "ai")
            elif asked < COMPANY_AI_CHECKS_PER_SCAN:
                checkpoint(db)  # no write lock held while the AI thinks
                seen[key] = verify_with_llm(job, check_job(job))
                asked += 1
                if progress is not None:
                    progress.tick()
            else:
                seen[key] = None
        if seen[key] is not None:
            apply_company_check(job, seen[key])
    if run and asked:
        run.log(f"Company check: the AI looked at {asked} unknown compan{'y' if asked == 1 else 'ies'}")
    return asked


def suspicious_message(job: Job) -> str:
    flags = [r.removeprefix("⚠ ") for r in (job.company_check or {}).get("reasons") or [] if r.startswith("⚠")]
    why = f" ({'; '.join(flags[:2])})" if flags else ""
    return (f"⚠ {job.company_name} looks like a possible fraud{why}, so nothing is sent to it. If you're sure "
            "it's real, mark it legit first.")


def company_hold_reason(job: Job) -> str:
    reasons = [r.removeprefix("⚠ ") for r in (job.company_check or {}).get("reasons") or []]
    why = f" ({reasons[0]})" if reasons else ""
    return (f"Not sent automatically: {job.company_name} isn't a verified company{why}. Check it, then submit it "
            "yourself — or mark it legit so the agent applies to it automatically.")
