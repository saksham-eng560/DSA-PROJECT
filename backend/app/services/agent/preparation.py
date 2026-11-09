"""Preparing a kept (or matched) job: the full description, the resume to send, a cover letter and answers to
the questions the ATS publishes. Then the form is filled (``submission``), or the application waits for you."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import checkpoint
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform
from app.models.job import Job
from app.models.resume import Resume
from app.models.user import User
from app.scrapers.ats_detect import parse_ats_url
from app.scrapers.greenhouse import GreenhouseScraper
from app.services.agent.documents import render_tailored_pdf, resume_strategy
from app.services.agent.jobs import enrich_job, get_master_resume
from app.services.agent.runs import PREPARED, RunLog, is_prepared
from app.services.agent.scoring import auto_apply_threshold
from app.services.agent.submission import ready_or_submit, stage_application
from app.services.application_service import set_status
from app.services.cover_letter import generate_cover_letter
from app.services.job_matcher import evaluate_match
from app.services.notifier import notify
from app.services.question_answerer import answer_questions, mappings_dict
from app.services.resume_tailor import light_tailor, tailor_resume

logger = logging.getLogger(__name__)


def prepare_application(db: Session, application_id: str, stage: bool | None = None) -> Application:
    app = db.get(Application, uuid.UUID(str(application_id)))
    if app is None:
        raise ValueError("Application not found")
    user = db.get(User, app.user_id)
    if app.status != ApplicationStatus.PREPARING:
        # Stale task: the swipe was undone, the job was skipped, or a duplicate task already prepared it.
        logger.info("Skipping preparation of %s: status is %s", app.id, app.status.value)
        return app
    run = RunLog(db, user, "prepare", "system")
    run.log(f"Preparing {app.job.role_title} @ {app.job.company_name}")
    if is_prepared(app):  # prepared again (e.g. "Prepare application"): not finished until this run is
        app.match_details = {k: v for k, v in (app.match_details or {}).items() if k != PREPARED}
    try:
        master = get_master_resume(db, user)
        if master is None:
            raise ValueError("No master resume uploaded")
        job = app.job
        checkpoint(db)
        if enrich_job(db, job):
            run.log("Fetched the full job description from the posting")
        if app.match_score is None:  # e.g. imported before a resume existed; score it for the reviewer
            evaluation = evaluate_match(master.parsed_content, job, user.prefs, auto_apply_threshold(user.prefs), use_llm=True)
            app.match_score, app.match_reasoning, app.match_details = (
                evaluation["match_score"], evaluation["reasoning"], evaluation)

        resume = _resume_to_send(db, user, app, master, job, run)
        checkpoint(db)
        if _undone(db, app, run):
            return app
        if user.prefs.get("cover_letter_enabled", True):
            letter = generate_cover_letter(resume.parsed_content, job)
            app.cover_letter = letter["cover_letter"]
            run.log(f"Cover letter generated ({letter['method']}, tone={letter['tone']})")
        if app.ats_platform == ATSPlatform.GREENHOUSE:
            _pre_answer_greenhouse(user, app, resume.parsed_content, run)

        run.run.applications_prepared = 1
        app.match_details = {**(app.match_details or {}), PREPARED: datetime.now(UTC).isoformat()}
        checkpoint(db)
        if _undone(db, app, run):
            return app
        should_stage = settings.AUTO_STAGE_APPLICATIONS if stage is None else stage
        if should_stage:
            stage_application(db, str(app.id), run=run)
        else:
            ready_or_submit(db, user, app, run)
        run.finish("completed")
    except Exception as exc:
        logger.exception("Preparation failed for %s", application_id)
        if not db.is_active:  # e.g. "database is locked" mid-flush: the failure itself must still be saved
            db.rollback()
        run.log(f"Preparation failed: {exc}", level="error")
        app.error_log = f"{datetime.now(UTC).isoformat()} prepare: {exc}"
        set_status(db, app, ApplicationStatus.FAILED, "agent", f"Preparation failed: {exc}")
        run.finish("failed")
        notify(db, user, "agent_error", f"Could not prepare {app.job.company_name} application", str(exc)[:300],
               link=f"/dashboard/applications/{app.id}")
    return app


def _resume_to_send(db: Session, user: User, app: Application, master: Resume, job: Job, run: RunLog) -> Resume:
    """Your original file, a lightly reordered copy or a truth-guarded rewrite (``resume_strategy``)."""
    strategy = resume_strategy(user, master)
    if strategy == "original":
        # Your own file, exactly as uploaded: your design, your words, nothing re-parsed or re-rendered.
        app.tailored_resume_id = None
        app.tailored_resume_pdf_url = master.original_file_url
        run.log("Using your original resume file, unchanged")
        return master
    checkpoint(db)  # never hold the write lock while waiting on the LLM
    tailored = (light_tailor if strategy == "light" else tailor_resume)(master.parsed_content, job)
    resume = Resume(
        user_id=user.id,
        label=f"{job.company_name} — {job.role_title}"[:255],
        parsed_content=tailored["tailored_resume"],
        is_master=False,
        parent_resume_id=master.id,
        tailored_for_job_id=job.id,
        changes_made=tailored["changes_made"] + [f"[guard] {v}" for v in tailored["violations"]],
        version=1,
    )
    db.add(resume)
    db.flush()
    app.tailored_resume_id = resume.id
    app.tailored_resume_pdf_url = render_tailored_pdf(user, resume)
    run.log(f"Tailored resume ({tailored['method']}), {len(tailored['changes_made'])} changes, "
            f"{len(tailored['violations'])} truthfulness corrections")
    return resume


def _pre_answer_greenhouse(user: User, app: Application, resume_content: dict[str, Any], run: RunLog) -> None:
    """Greenhouse publishes a job's questions: answer them now, so you review them before the form is filled."""
    job = app.job
    try:
        ref = parse_ats_url(job.application_url or job.source_url)
        if ref.board and ref.job_id:
            questions = [q for q in GreenhouseScraper().application_questions(ref.board, ref.job_id)
                         if q["field_type"] != "file" and not q["question"].lower().startswith(("first name", "last name", "email", "phone"))]
            app.custom_answers = answer_questions(questions, resume_content, user.prefs,
                                                  mappings_dict(user.field_mappings), job)
            run.log(f"Pre-answered {len(questions)} Greenhouse questions")
    except Exception as exc:  # noqa: BLE001
        run.log(f"Could not pre-fetch Greenhouse questions: {exc}", level="warning")


def _undone(db: Session, app: Application, run: RunLog) -> bool:
    """True (and the run is closed) when you undid or skipped the job while it was being prepared."""
    db.refresh(app, attribute_names=["status", "review_decision"])
    if app.status == ApplicationStatus.PREPARING:
        return False
    run.log(f"Preparation stopped: the job is now {app.status.value.replace('_', ' ')}")
    run.finish("cancelled")
    return True
