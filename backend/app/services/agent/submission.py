"""Filling the form (staging), the checks before anything is sent, and submitting an approved application.

DIRECTIVE 2: nothing is ever submitted without explicit user approval. Keeping a job in Swipe Review (with
``auto_submit_kept``) is that approval once the form is filled with every required answer; anything the agent
is unsure about (rules #4 and #7) stops in "Needs approval". A failed form is retried once (rule #10).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.automation import browser
from app.core.database import checkpoint
from app.core.storage import get_storage
from app.models.application import Application
from app.models.enums import STATUS_RANK, ApplicationStatus, ATSPlatform
from app.models.user import User, UserFieldMapping
from app.schemas.resume_content import ResumeContent
from app.services.agent.documents import render_tailored_pdf, resume_filename, store_screenshot, temp_file
from app.services.agent.internshala import (
    INTERNSHALA_MISSING,
    INTERNSHALA_RECHECK_SECONDS,
    bot_apply_requested,
    internshala_auto_submit_allowed,
    internshala_blocker,
    internshala_missing,
    internshala_over_limit,
    internshala_ready,
    internshala_session_expired,
    is_internshala_job,
    run_on_internshala_account,
)
from app.services.agent.jobs import application_platform, company_hold_reason, get_master_resume, suspicious_message
from app.services.agent.runs import RunLog
from app.services.application_service import set_status
from app.services.company_verifier import SUSPICIOUS, is_trusted
from app.services.notifier import notify
from app.services.pdf_generator import render_cover_letter_pdf
from app.services.question_answerer import FACTUAL, answer_questions, learnable_key, mappings_dict
from app.services.rate_limiter import rate_limiter
from app.services.text_utils import normalize_text
from app.submitters import CandidatePacket, InternshalaSubmitter, SubmissionResult, get_submitter
from app.worker import dispatch

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- may it be sent?
def unanswered_questions(app: Application, trust_generated: bool = False) -> list[str]:
    """Questions that must be answered by you before a submission.

    Blank required answers and every eligibility question the agent is unsure about (visa, work
    authorization, record checks...) always block. With ``trust_generated`` the agent's own answers
    to open-ended questions ("Why do you want to work here?") don't.
    """
    blocking = []
    for a in app.custom_answers or []:
        question = str(a.get("question") or "")
        answer = str(a.get("answer") or "").strip()
        factual = bool(FACTUAL.search(normalize_text(question)))
        if (a.get("required") and not answer) or (factual and (not answer or a.get("needs_user_review"))):
            blocking.append(question)
        elif a.get("needs_user_review") and not trust_generated:
            blocking.append(question)
    return blocking


def _may_auto_submit(user: User, app: Application) -> bool:
    """Sites that take applications only from your own account are never sent without your click,
    except Internshala with the bot ready and either "Submit automatically" on or your
    "Apply with the bot" click on this application."""
    if is_internshala_job(app.job):
        return internshala_auto_submit_allowed(user, app)
    return not (app.job.raw_data or {}).get("apply_on_site")


def direct_submit_blocker(user: User, app: Application, probe: bool = True) -> str | None:
    """Why the agent can't submit this application itself (None = your one click submits it).

    Boards like Internshala only take applications from your own logged-in account; Internshala
    can, once you turn on its bot and sync your login. Nothing is sent without a working browser either.
    ``probe=False`` (listing pages): the browser check answers from the last launch, never starts Chromium.
    """
    if app.job.company_verdict == SUSPICIOUS and not is_trusted(app.job, user.prefs):
        return suspicious_message(app.job)
    if is_internshala_job(app.job):
        blocker = internshala_blocker(user, app)
        if blocker:
            return blocker
    elif site := (app.job.raw_data or {}).get("apply_on_site"):
        return f"{site} needs your own {site} login: apply there, then click “I Applied”."
    return browser.browser_problem(probe=probe)  # e.g. Chromium isn't installed: say so now, not after a failed submission


def ready_or_submit(db: Session, user: User, app: Application, run: RunLog | None = None) -> None:
    """Kept jobs go straight to submission when nothing needs a human; everything else waits for review."""
    if app.auto_submit and app.review_decision == "keep" and _may_auto_submit(user, app):
        blockers = unanswered_questions(app, trust_generated=bool(user.prefs.get("trust_generated_answers", True)))
        # Only verified companies are applied to on their own; your "Apply with the bot" click is your OK for this one
        if not app.needs_manual_review and not blockers and not is_trusted(app.job, user.prefs) \
                and not bot_apply_requested(app):
            app.needs_manual_review = True
            app.manual_review_reason = company_hold_reason(app.job)
            if run:
                run.log(f"Waiting for you: {app.job.company_name} isn't a verified company")
        if not app.needs_manual_review and not blockers:
            set_status(db, app, ApplicationStatus.APPROVED, "user", "Approved when you kept it in Swipe Review")
            if run:
                run.log("Everything answered — submitting (you kept this job in Swipe Review)")
            dispatch.enqueue("submit_application", str(app.id), after_commit=db)
            return
        if run:
            run.log("Needs your review before submitting: "
                    + (app.manual_review_reason or f"{len(blockers)} question(s) need an answer"))
    _mark_ready(db, user, app)


def _mark_ready(db: Session, user: User, app: Application) -> None:
    set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent", "Ready for your review")
    warn = f" ⚠️ {app.manual_review_reason}" if app.needs_manual_review and app.manual_review_reason else ""
    notify(
        db, user, "application_ready",
        f"Review application: {app.job.role_title} @ {app.job.company_name}",
        f"Match score {app.match_score or '—'}. Tailored resume, cover letter and answers are ready.{warn}",
        link=f"/dashboard/submit?id={app.id}",  # where you check it and send it
        data={"application_id": str(app.id), "match_score": app.match_score},
    )


# --------------------------------------------------------------------------- driving the browser
def build_packet(db: Session, user: User, app: Application, resume_path: str | None, cover_path: str | None) -> Any:
    resume = app.tailored_resume or get_master_resume(db, user)
    rc = ResumeContent.model_validate(resume.parsed_content if resume else {})
    info = rc.personal_info
    name = (info.name or user.full_name or "").split(" ")
    mappings = mappings_dict(user.field_mappings)
    job = app.job

    def resolver(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return answer_questions(questions, rc.to_dict(), user.prefs, mappings, job)

    current = rc.experience[0] if rc.experience else None
    credentials = dict(user.ats_credentials or {})
    if mappings.get("workday_password") and "workday_password" not in credentials:
        credentials["workday_password"] = mappings["workday_password"]
    return CandidatePacket(
        first_name=name[0] if name else "",
        last_name=" ".join(name[1:]) if len(name) > 1 else "",
        email=info.email or user.email,
        phone=info.phone or user.phone or "",
        location=info.location or user.location or "",
        linkedin=info.linkedin or user.linkedin_url or "",
        github=info.github,
        portfolio=info.portfolio or mappings.get("website", ""),
        current_company=current.company if current else "",
        current_title=current.title if current else "",
        resume_path=resume_path,
        cover_letter_text=app.cover_letter,
        cover_letter_path=cover_path,
        answers=list(app.custom_answers or []),
        resolve_answers=resolver,
        ats_credentials=credentials,
        linkedin_cookie=user.linkedin_session_cookie,
        internshala_session=user.internshala_session if is_internshala_job(job) else None,
        internshala_user_agent=user.internshala_user_agent if is_internshala_job(job) else None,
        application_url=job.application_url or job.source_url,
        company_name=job.company_name,
        role_title=job.role_title,
        # Your corrections from the "Ready to submit" queue beat everything the agent worked out
        overrides={str(k): "" if v is None else str(v) for k, v in (app.field_overrides or {}).items()},
    )


def _run_submitter(db: Session, user: User, app: Application, submit: bool) -> SubmissionResult:
    def drive() -> SubmissionResult:
        return _drive_submitter(db, user, app, submit)

    return run_on_internshala_account(user, drive) if is_internshala_job(app.job) else drive()


def _drive_submitter(db: Session, user: User, app: Application, submit: bool) -> SubmissionResult:
    storage = get_storage()
    if not app.tailored_resume_pdf_url:
        resume = app.tailored_resume or get_master_resume(db, user)
        if resume is None:
            raise ValueError("No resume available")
        app.tailored_resume_pdf_url = render_tailored_pdf(user, resume)
    pdf = storage.read(app.tailored_resume_pdf_url)
    ext = os.path.splitext(app.tailored_resume_pdf_url)[1].lower() or ".pdf"  # your original may be a .docx
    cover_pdf = render_cover_letter_pdf(app.cover_letter, {"name": user.full_name, "email": user.email}) if app.cover_letter else None
    submitter = (InternshalaSubmitter() if is_internshala_job(app.job)
                 else get_submitter(app.ats_platform or application_platform(app.job)))
    with temp_file(pdf, ext, resume_filename(user, ext)) as resume_path:
        if cover_pdf:
            with temp_file(cover_pdf, ".pdf", "Cover_Letter.pdf") as cover_path:
                packet = build_packet(db, user, app, resume_path, cover_path)
                return submitter.submit(packet) if submit else submitter.stage(packet)
        packet = build_packet(db, user, app, resume_path, None)
        return submitter.submit(packet) if submit else submitter.stage(packet)


def _merge_answers(existing: list[dict[str, Any]] | None, new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for answer in (existing or []) + new:
        key = (answer.get("question") or "").strip().lower()
        if key:
            merged[key] = {**merged.get(key, {}), **answer}
    return list(merged.values())


def _internshala_already_applied(db: Session, user: User, app: Application, run: RunLog | None, by_agent: bool) -> None:
    """Internshala shows "Already Applied": track it as applied instead of failing."""
    note = "Internshala shows this internship as already applied"
    app.needs_manual_review = False
    app.manual_review_reason = None
    if by_agent:  # an earlier attempt of ours went through without a confirmation
        set_status(db, app, ApplicationStatus.APPLIED, "agent", note)
        notify(db, user, "application_submitted", f"✅ Applied: {app.job.role_title} @ {app.job.company_name}",
               "Internshala confirms your application was sent.", link=f"/dashboard/applications/{app.id}")
    else:
        mark_self_applied(db, user, app, note=f"{note}; tracking it from now on")
    if run:
        run.log(note)


# --------------------------------------------------------------------------- staging
def stage_application(db: Session, application_id: str, run: RunLog | None = None) -> Application:
    """Fill the form and take a screenshot WITHOUT submitting, then wait for approval."""
    if run is not None:  # part of a preparation, which reports any failure itself
        return _stage(db, application_id, run)
    try:
        return _stage(db, application_id)
    except Exception as exc:  # "Re-fill form" / "Apply with the bot": never left "preparing" for good, without a word
        logger.exception("Filling the form failed for %s", application_id)
        if not db.is_active:
            db.rollback()
        app = db.get(Application, uuid.UUID(str(application_id)))
        if app is None or app.status != ApplicationStatus.PREPARING:
            raise
        user = db.get(User, app.user_id)
        app.error_log = f"{datetime.now(UTC).isoformat()} stage: {exc}"
        set_status(db, app, ApplicationStatus.FAILED, "agent", f"Filling the form failed: {exc}")
        notify(db, user, "agent_error", f"Could not fill the {app.job.company_name} form", str(exc)[:300],
               link=f"/dashboard/applications/{app.id}")
        return app


def _stage(db: Session, application_id: str, run: RunLog | None = None) -> Application:
    app = db.get(Application, uuid.UUID(str(application_id)))
    user = db.get(User, app.user_id)
    internshala = is_internshala_job(app.job)
    site = (app.job.raw_data or {}).get("apply_on_site") or ("Internshala" if internshala else None)
    # Boards like Internshala only take applications from your own logged-in account (the opt-in
    # Internshala bot can, with the login the extension syncs: then the form is filled like any other).
    # Any Internshala posting counts, however it was added (e.g. "Add job by URL"), so the bot never
    # opens your account while it's off.
    if site and not (internshala and internshala_ready(user)):
        missing = internshala_missing(user) if internshala else None
        if missing in (None, "bot_off"):  # you apply there yourself (turning the bot on restores auto-submit)
            app.auto_submit = False
        app.needs_manual_review = True
        app.manual_review_reason = (INTERNSHALA_MISSING[missing] if missing and missing != "bot_off" else
                                    f"{site} needs your own {site} login: open the application form, apply there, "
                                    "then click “I Applied” so the agent keeps tracking it.")
        app.staged_at = datetime.now(UTC)
        if run:
            run.log(f"{site} posting: your resume and answers are ready; apply on {site} yourself")
        if missing == "expired":  # the one "Internshala session expired" notice covers every waiting application
            set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent", "Waiting for a working Internshala login")
        else:
            _mark_ready(db, user, app)
        return app
    result = None
    checkpoint(db)
    for attempt in (1, 2):  # rule #10: retry once
        result = _run_submitter(db, user, app, submit=False)
        if result.success or result.session_expired or result.stage != "failed":  # e.g. already applied: final
            break
        if run:
            run.log(f"Staging attempt {attempt} failed: {result.error}", level="warning")
    db.refresh(app, attribute_names=["status"])
    if app.status != ApplicationStatus.PREPARING:  # e.g. you clicked "I Applied" while the form was being filled
        if run:
            run.log(f"Form filled, but the job is now {app.status.value.replace('_', ' ')}; leaving it as is")
        return app
    if result.stage == "already_applied":
        _internshala_already_applied(db, user, app, run, by_agent=False)
        return app
    if result.screenshot and not result.session_expired:  # a login / sign-up page is not your filled form
        app.form_screenshot_url = store_screenshot(user.id, result.screenshot)
    app.form_fields = result.fields
    if result.answers:
        app.custom_answers = _merge_answers(app.custom_answers, result.answers)
    app.staged_at = datetime.now(UTC)
    if result.session_expired and internshala:
        internshala_session_expired(db, user)
    elif result.session_expired:
        user.linkedin_session_valid = False
        notify(db, user, "session_expired", "LinkedIn session expired",
               "Re-sync your LinkedIn session from the browser extension to use Easy Apply.", link="/dashboard/settings")
    if not result.success:
        app.needs_manual_review = True
        app.manual_review_reason = (result.error if result.stage == "unavailable"  # closed / external / profile gate
                                    else f"Automatic form filling failed: {result.error}. You can still apply manually.")
        app.error_log = f"{datetime.now(UTC).isoformat()} stage: {result.error}"
    else:
        app.needs_manual_review = result.needs_manual_review
        app.manual_review_reason = result.review_reason
    if result.session_expired and internshala:  # waits for a fresh login; one "session expired" notice covers all
        set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent", "Waiting for a working Internshala login")
        return app
    if run:
        run.log(f"Staged form on {app.ats_platform.value if app.ats_platform else 'unknown'}: "
                f"{len([f for f in result.fields if f['status'] == 'filled'])} fields filled"
                + (f", review needed: {app.manual_review_reason}" if app.needs_manual_review else ""))
    ready_or_submit(db, user, app, run)
    return app


# --------------------------------------------------------------------------- approval & submission
def approve_application(db: Session, app: Application, cover_letter: str | None = None,
                        custom_answers: list[dict[str, Any]] | None = None, note: str = "Approved by user") -> Application:
    if app.status not in (ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.FAILED, ApplicationStatus.MATCHED):
        raise ValueError(f"Cannot approve an application in status '{app.status.value}'")
    if cover_letter is not None:
        app.cover_letter = cover_letter
    if custom_answers is not None:
        app.custom_answers = [{**a, "needs_user_review": False, "source": a.get("source") or "user"} for a in custom_answers]
        remember_answers(db, app.user_id, custom_answers)
    app.retry_count = 0
    set_status(db, app, ApplicationStatus.APPROVED, "user", note)
    db.flush()
    dispatch.enqueue("submit_application", str(app.id), after_commit=db)
    return app


def mark_self_applied(db: Session, user: User, app: Application, applied_on: date | None = None,
                      note: str | None = None) -> bool:
    """"I Applied": you applied on your own. The agent stops working on it and tracks it from now on.

    Returns False when the application is already at "applied" or further along (nothing moves backwards).
    """
    if STATUS_RANK.get(app.status, 0) >= STATUS_RANK[ApplicationStatus.APPLIED] and app.status != ApplicationStatus.WITHDRAWN:
        return False
    app.auto_submit = False
    app.needs_manual_review = False
    app.manual_review_reason = None
    set_status(db, app, ApplicationStatus.APPLIED, "user", note or "You applied on your own")
    if applied_on is not None and applied_on <= datetime.now(UTC).date():
        app.submitted_at = datetime(applied_on.year, applied_on.month, applied_on.day, 12, tzinfo=UTC)
    job = app.job
    notify(
        db, user, "self_applied", f"📌 Tracking: {job.role_title} @ {job.company_name}",
        "You applied on your own. AutoApply AI now watches your inbox for replies from "
        f"{job.company_name} and will tell you about every update (acknowledgement, test, interview, offer).",
        link=f"/dashboard/applications/{app.id}",
        data={"application_id": str(app.id), "company": job.company_name, "role": job.role_title},
    )
    return True


def remember_answers(db: Session, user_id: uuid.UUID, answers: list[dict[str, Any]]) -> None:
    """Save approved answers to standard questions (sponsorship, work authorization...) as field mappings."""
    known = set(db.scalars(select(UserFieldMapping.field_name).where(UserFieldMapping.user_id == user_id)))
    for answer in answers:
        value = str(answer.get("answer") or "").strip()
        key = learnable_key(str(answer.get("question") or ""))
        if key and value and key not in known:
            db.add(UserFieldMapping(user_id=user_id, field_name=key, field_value=value, field_type="text"))
            known.add(key)


SUBMISSION_STALE_MINUTES = 15  # one attempt takes minutes at most (the worker's limit is 10): older means it died


def _claim_submission(db: Session, user: User, app: Application) -> bool:
    """Only one worker sends an application: the one that marks it "being sent" first.

    Another copy of the task (re-queued by a restart while the local scheduler was sending it, or by a second API
    worker) leaves it alone and looks again once that attempt must be over. An attempt that never finished may
    have gone out, so the application is held for you instead of being sent a second time.
    """
    now = datetime.now(UTC)
    claimed = db.execute(
        update(Application)
        .where(Application.id == app.id, Application.status == ApplicationStatus.APPROVED,
               Application.submitting_at.is_(None))
        .values(submitting_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.refresh(app, attribute_names=["status", "submitting_at"])
    if claimed:
        return True
    if app.status != ApplicationStatus.APPROVED or app.submitting_at is None:  # the other worker is done with it
        return False
    age = now - app.submitting_at
    stale = timedelta(minutes=SUBMISSION_STALE_MINUTES)
    if age < stale:
        logger.info("Application %s is already being sent (since %s)", app.id, app.submitting_at.isoformat())
        dispatch.enqueue("submit_application", str(app.id), countdown=int((stale - age).total_seconds()) + 30,
                         after_commit=db)
        return False
    reason = ("The app stopped while sending this application, so it may already have gone out. Check your e-mail or "
              f"{app.job.company_name}'s site: click “I Applied” if it did, or Submit to send it again.")
    app.submitting_at = None
    app.needs_manual_review = True
    app.manual_review_reason = reason
    set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent", "Interrupted while being sent")
    notify(db, user, "application_held", f"Check: {app.job.role_title} @ {app.job.company_name}", reason,
           link=f"/dashboard/submit?id={app.id}")
    return False


def submit_application(db: Session, application_id: str) -> Application:
    """Submit an APPROVED application. Never called for unapproved applications (DIRECTIVE 2)."""
    app = db.get(Application, uuid.UUID(str(application_id)))
    if app is None:
        raise ValueError("Application not found")
    if app.status != ApplicationStatus.APPROVED:
        logger.warning("Refusing to submit application %s in status %s", app.id, app.status)
        return app
    user = db.get(User, app.user_id)
    blocker = direct_submit_blocker(user, app)
    if blocker:  # e.g. the Internshala bot was turned off, or its login expired, after you approved
        app.needs_manual_review = True
        app.manual_review_reason = blocker
        set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent", blocker)
        notify(db, user, "application_held", f"Not sent: {app.job.role_title} @ {app.job.company_name}",
               f"{blocker} It's back in Ready to submit.", link=f"/dashboard/submit?id={app.id}")
        return app
    internshala = is_internshala_job(app.job)
    platform = "internshala" if internshala else (app.ats_platform or ATSPlatform.UNKNOWN).value
    allowed, reason = rate_limiter.can_apply(str(user.id), platform, int(user.prefs.get("max_applications_per_day") or 25))
    wait = int(rate_limiter.cooldown_seconds(platform)) + 30
    if allowed and internshala and (reason := internshala_over_limit(db, user)):
        allowed, wait = False, INTERNSHALA_RECHECK_SECONDS
    if not allowed:
        app.notes = f"Waiting: {reason}"
        dispatch.enqueue("submit_application", str(app.id), countdown=wait, after_commit=db)
        return app
    if not _claim_submission(db, user, app):
        return app

    run = RunLog(db, user, "apply", "user")
    run.log(f"Submitting {app.job.role_title} @ {app.job.company_name} via {platform}")
    checkpoint(db)  # the claim is visible to every other worker before a browser opens
    try:
        result = _run_submitter(db, user, app, submit=True)
    except Exception as exc:  # e.g. the resume file is gone: a failed attempt, never an application stuck "approved"
        logger.exception("Submitting %s crashed", app.id)
        if not db.is_active:
            db.rollback()
        result = SubmissionResult(False, "failed", error=f"{type(exc).__name__}: {exc}")
    app.submitting_at = None  # this attempt is over, whatever came of it
    if result.screenshot:
        key = store_screenshot(user.id, result.screenshot)
        if result.success:
            app.confirmation_screenshot_url = key
    if result.answers:
        app.custom_answers = _merge_answers(app.custom_answers, result.answers)

    if result.success and result.stage == "submitted":
        rate_limiter.record_application(str(user.id), platform)
        app.confirmation_number = result.confirmation_number
        set_status(db, app, ApplicationStatus.APPLIED, "agent", "Submitted successfully")
        run.run.applications_submitted = 1
        run.log("Application submitted ✅")
        run.finish("completed")
        notify(db, user, "application_submitted", f"✅ Applied: {app.job.role_title} @ {app.job.company_name}",
               "Your application was submitted.", link=f"/dashboard/applications/{app.id}")
        return app
    if result.success and result.stage == "dry_run":
        app.needs_manual_review = True
        app.manual_review_reason = "Dry-run mode (SUBMISSION_DRY_RUN=true): the final Submit click was skipped."
        set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "system", "Dry run completed")
        run.log("Dry run — submit click skipped")
        run.finish("completed")
        return app

    if result.stage == "already_applied":
        _internshala_already_applied(db, user, app, run, by_agent=bool(app.retry_count))
        run.finish("completed")
        return app
    app.retry_count = (app.retry_count or 0) + 1
    app.error_log = f"{datetime.now(UTC).isoformat()} submit: {result.error}"
    run.log(f"Submission failed: {result.error}", level="error")
    if result.session_expired and internshala:  # back to your review queue; Submit works again after a re-sync
        internshala_session_expired(db, user)
        app.needs_manual_review = True
        app.manual_review_reason = "Internshala session expired — open Internshala in Chrome, click Sync in the extension, then submit again."
        set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent", "Internshala session expired")
        run.finish("failed")
        return app
    if result.session_expired:
        notify(db, user, "session_expired", "LinkedIn session expired",
               "Re-sync your LinkedIn session to finish this Easy Apply.", link="/dashboard/settings")
    if app.retry_count <= 1 and not result.session_expired and result.stage == "failed":
        run.log("Retrying once")
        run.finish("failed")
        dispatch.enqueue("submit_application", str(app.id), countdown=30, after_commit=db)
        return app
    set_status(db, app, ApplicationStatus.FAILED, "agent", f"Submission failed: {result.error}")
    run.finish("failed")
    notify(db, user, "application_failed", f"Could not submit {app.job.company_name} application",
           f"{(result.error or 'Unknown error').rstrip('.')}. Open the application to apply manually.",
           link=f"/dashboard/applications/{app.id}")
    return app
