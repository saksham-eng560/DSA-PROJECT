"""A scan, start to finish: search every source, keep the postings that fit, save them, check the companies,
score them against your resume, then fill Swipe Review (or, outside it, queue the best matches for preparation
within your daily budget, rule #3)."""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import checkpoint
from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.models.job import Job
from app.models.user import User
from app.scrapers import SCRAPERS, ScraperError, SearchQuery, fetch_job_from_url
from app.services.agent.discovery import discover_jobs, scan_platforms, select_postings
from app.services.agent.jobs import (
    SeenPostings,
    backfill_company_checks,
    embed_jobs,
    ensure_application,
    get_master_resume,
    known_source_urls,
    resume_embedding,
    upsert_job,
    user_source_urls,
    verify_companies,
)
from app.services.agent.review import is_swipe_mode, keep_application, skip_ineligible_waiting
from app.services.agent.runs import RunLog
from app.services.agent.scoring import evaluate_application, revive_held_back, score_applications, swiped
from app.services.application_service import set_status
from app.services.company_verifier import SUSPICIOUS
from app.services.embeddings import cosine_similarity
from app.services.job_matcher import priority_key
from app.services.location_focus import get_focus, get_season, location_tier, season_status
from app.services.notifier import notify
from app.services.rate_limiter import rate_limiter
from app.services.scan_progress import ScanCancelled, ScanProgress
from app.worker import dispatch

logger = logging.getLogger(__name__)


def run_scan(db: Session, user: User, trigger: str = "user", platforms: list[str] | None = None,
             auto_prepare: bool = True, run: RunLog | None = None) -> AgentRun:
    run = run or RunLog(db, user, "scan", trigger)
    prefs = user.prefs
    chosen = scan_platforms(prefs, platforms)
    progress = ScanProgress(db, run.run, [p for p in chosen if p in SCRAPERS])
    try:
        master = get_master_resume(db, user)
        skip_ineligible_waiting(db, user)  # cards your current settings rule out don't hold room in the deck
        query = SearchQuery.from_preferences(prefs, limit=int(prefs.get("max_jobs_per_source") or settings.MAX_JOBS_PER_SOURCE))
        query = dataclasses.replace(query, known_urls=known_source_urls(db))
        run.log(f"Scanning {', '.join(chosen)} for {', '.join(query.keywords) or 'all roles'}"
                + (" (internships only)" if query.internships_only else ""))
        progress.set_phase("discovering", f"Searching {len(progress.sources)} job sources at once")
        scraped = discover_jobs(query, chosen, run, progress=progress, tick=progress.tick)
        scraped, notes = select_postings(scraped, prefs, master.parsed_content if master else None, query.focus,
                                         have_internshala=user_source_urls(db, user, "internshala.com/"),
                                         have=user_source_urls(db, user, ""))
        for note in notes:
            run.log(note)
        progress.found = len(scraped)
        progress.to_save = len(scraped)
        progress.set_phase("saving", f"Saving {len(scraped)} postings")
        new_apps: list[Application] = []
        found_again: list[Application] = []
        jobs: list[Job] = []
        seen = SeenPostings.load(db)  # Bloom filter: "certainly new" postings skip the duplicate query
        for idx, sj in enumerate(scraped):
            try:
                with db.begin_nested():
                    job, _created = upsert_job(db, sj, seen)
                    app, created = ensure_application(db, user, job)
            except Exception as exc:  # noqa: BLE001
                run.log(f"Could not store job {sj.source_url}: {exc}", level="error")
                continue
            jobs.append(job)
            if created:
                new_apps.append(app)
            else:
                found_again.append(app)
            progress.saved = idx + 1
            progress.new = len(new_apps)
            if idx % 25 == 24:
                progress.tick()
        run.run.jobs_discovered = len(new_apps)
        run.log(f"Discovered {len(scraped)} postings, {len(new_apps)} new for you")
        if scraped:
            run.log(seen.summary())
        backfill_company_checks(db)
        progress.set_phase("saving", "Checking the companies behind the new postings")
        verify_companies(db, [a.job for a in new_apps], run, progress)
        flagged = sum(1 for a in new_apps if a.job.company_verdict == SUSPICIOUS)
        if flagged:
            run.log(f"Company check: {flagged} posting{'s' if flagged != 1 else ''} with scam signs (skipped)")
        progress.set_phase("saving", f"Reading {len(new_apps)} new postings")  # embedding may call an API
        embed_jobs(db, jobs)
        checkpoint(db)

        resume_vec = resume_embedding(db, master) if master else None
        if master is None:
            run.log("No master resume uploaded — skipping matching", level="warning")
        # Rank by vector similarity so the most promising jobs get the (costly) LLM evaluation
        if resume_vec is not None:
            new_apps.sort(key=lambda a: cosine_similarity(resume_vec, a.job.description_embedding), reverse=True)
        # Near misses an earlier scan had no room for, found again while the deck is thin: shown this time
        revived = revive_held_back(db, user, new_apps, found_again, master)
        if revived:
            run.log(f"Swipe Review is thin: {_count(len(revived), 'near miss', 'near misses')} found before, "
                    "when the deck was full, get their turn")
        to_score = new_apps + revived
        progress.to_score = len(to_score)
        progress.set_phase("scoring", f"Scoring {len(to_score)} new jobs against your resume")
        score_applications(db, user, to_score, master, resume_vec, run, progress)
        matched = [a for a in to_score if a.status == ApplicationStatus.MATCHED]
        run.run.jobs_matched = len(matched)
        user.last_scan_at = datetime.now(UTC)
        progress.set_phase("finishing", "Wrapping up")

        if is_swipe_mode(prefs):
            auto_keep = prefs.get("auto_keep_min_score")
            kept = 0
            if auto_keep is not None and master is not None:
                for app in sorted(matched, key=lambda a: priority_key(a.match_score, a.job.deadline_date)):
                    if (app.match_details or {}).get("near_miss"):
                        continue  # outside your settings, shown only because the deck was thin: yours to decide
                    if (app.match_score or 0) >= int(auto_keep) and not swiped(db, app):
                        keep_application(db, user, app, decided_by="agent",
                                         note=f"Kept automatically (score {app.match_score} ≥ {auto_keep})")
                        kept += 1
            waiting = len(matched) - kept
            run.log(f"{waiting} jobs are waiting for you in Swipe Review"
                    + (f"; {kept} kept automatically (score ≥ {auto_keep})" if kept else ""))
            progress.finish("completed", f"{run.run.jobs_discovered} new jobs, {waiting} waiting for your swipe")
            run.finish("completed")
            notify(db, user, "scan_completed", "Job scan finished",
                   f"{run.run.jobs_discovered} new jobs — {waiting} waiting for your swipe.", link="/dashboard/review",
                   data={"run_id": str(run.run.id)})
            return run.run

        run.log(f"{len(matched)} jobs matched your threshold ({prefs.get('auto_apply_threshold')})")
        if auto_prepare and master is not None:
            queued = queue_preparations(db, user, matched, run)
            run.log(f"Queued {queued} applications for preparation")
        progress.finish("completed", f"{run.run.jobs_discovered} new jobs, {run.run.jobs_matched} matches")
        run.finish("completed")
        notify(db, user, "scan_completed", "Job scan finished",
               f"{run.run.jobs_discovered} new jobs, {run.run.jobs_matched} matches.", link="/dashboard/jobs",
               data={"run_id": str(run.run.id)})
    except ScanCancelled:
        run.log("Scan stopped by you; jobs already scored stay in Swipe Review")
        progress.finish("cancelled", "Stopped")
        run.finish("cancelled")
    except Exception as exc:
        logger.exception("Scan failed for user %s", user.id)
        if not db.is_active:  # a failed flush: roll back so the failure itself can be recorded
            db.rollback()
        run.log(f"Scan failed: {exc}", level="error")
        progress.finish("failed", f"Scan failed: {exc}"[:200])
        run.finish("failed")
        notify(db, user, "agent_error", "Job scan failed", str(exc)[:300], link="/dashboard/logs")
    return run.run


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def focus_rank(job: Job, prefs: dict[str, Any]) -> tuple[int, int]:
    """(location tier, season rank): prime city before the rest of the country, the target season first."""
    focus, season = get_focus(prefs), get_season(prefs)
    tier = location_tier(job.location, job.is_remote, focus) if focus else 0
    if season is None:
        return tier, 0
    state, _ = season_status(job.role_title, job.description, (job.raw_data or {}).get("terms"), season)
    return tier, 0 if state == "match" else 1


def queue_preparations(db: Session, user: User, matched: list[Application], run: RunLog | None = None) -> int:
    """Queue preparation for the best matches, respecting the daily application budget."""
    prefs = user.prefs
    budget = int(prefs.get("max_applications_per_day") or 25)
    in_flight = db.scalar(
        select(func.count()).select_from(Application).where(
            Application.user_id == user.id,
            Application.status.in_([ApplicationStatus.PREPARING, ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.APPROVED]),
        )
    ) or 0
    remaining = max(0, budget - rate_limiter.applications_today(str(user.id)) - int(in_flight))
    ordered = sorted(matched, key=lambda a: (*focus_rank(a.job, prefs), *priority_key(a.match_score, a.job.deadline_date)))
    count = 0
    for app in ordered[:remaining]:
        set_status(db, app, ApplicationStatus.PREPARING, "agent", "Queued for preparation")
        db.flush()
        dispatch.enqueue("prepare_application", str(app.id), after_commit=db)
        count += 1
    if run and len(ordered) > remaining:
        run.log(f"{len(ordered) - remaining} matches deferred (daily budget of {budget} reached)")
    return count


def import_job_url(db: Session, user: User, url: str) -> Application:
    """"Add job by URL": read the posting, save it and score it against your resume."""
    scraped = fetch_job_from_url(url)
    if scraped is None:
        raise ScraperError("Could not read a job posting at that URL")
    job, _ = upsert_job(db, scraped)
    embed_jobs(db, [job])
    app, _ = ensure_application(db, user, job)
    master = get_master_resume(db, user)
    if app.status == ApplicationStatus.DISCOVERED:
        evaluate_application(db, user, app, master, use_llm=True,
                             resume_vec=resume_embedding(db, master) if master else None)
    return app
