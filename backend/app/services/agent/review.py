"""Swipe Review (``review_mode="swipe"``, the default): scans never skip a job for a low score. Every job that
passes your hard filters waits on the deck; keeping one is your approval to prepare it and, with
``auto_submit_kept``, to submit it once the form is filled with every required answer."""

from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.models.job import Job
from app.models.user import User
from app.services.agent.jobs import get_master_resume
from app.services.application_service import set_status
from app.services.company_verifier import SUSPICIOUS, is_trusted
from app.services.intern_level import with_resume
from app.services.job_matcher import split_reasons
from app.worker import dispatch


def is_swipe_mode(prefs: dict[str, Any]) -> bool:
    return (prefs.get("review_mode") or "swipe") == "swipe"


# --------------------------------------------------------------------------- a full deck
# Your narrower wishes (tech focus, internship length, verified companies only) can leave a scan with a handful of
# cards. When fewer than ``fill_deck_to`` (default 40) wait in Swipe Review, the closest near misses fill the rest,
# each with the wish it misses as a heads-up. Your hard rules (avoided companies, excluded titles, not an
# internship for you, abroad, scams, another season) are never relaxed. 0 turns it off.
NEAR_MISS = "Outside your settings: "
DEFAULT_DECK_TARGET = 40


def deck_target(prefs: dict[str, Any]) -> int:
    try:
        return max(0, min(200, int(prefs.get("fill_deck_to", DEFAULT_DECK_TARGET))))
    except (TypeError, ValueError):
        return DEFAULT_DECK_TARGET


def awaiting_swipe(user_id: uuid.UUID) -> tuple[ColumnElement[bool], ...]:
    """The filter for your cards still waiting in Swipe Review."""
    return (Application.user_id == user_id,
            Application.status.in_((ApplicationStatus.DISCOVERED, ApplicationStatus.MATCHED)),
            Application.review_decision.is_(None))


def waiting_count(db: Session, user: User, exclude: Collection[uuid.UUID] = ()) -> int:
    query = select(func.count()).select_from(Application).where(*awaiting_swipe(user.id))
    if exclude:
        query = query.where(Application.id.not_in(list(exclude)))
    return db.scalar(query) or 0


def skip_suspicious_waiting(db: Session, user: User) -> int:
    """Jobs waiting in Swipe Review whose company looks like a scam are skipped (unless you marked it legit)."""
    if not user.prefs.get("skip_suspicious_companies", True):
        return 0
    waiting = db.scalars(
        select(Application).join(Job, Job.id == Application.job_id)
        .where(*awaiting_swipe(user.id), Job.company_verdict == SUSPICIOUS)).all()
    skipped = 0
    for app in waiting:
        if is_trusted(app.job, user.prefs):
            continue
        reasons = [r.removeprefix("⚠ ") for r in (app.job.company_check or {}).get("reasons") or [] if r.startswith("⚠")]
        app.match_reasoning = "Possible fraud: " + ("; ".join(reasons[:2]) or "the company check flagged this posting")
        set_status(db, app, ApplicationStatus.SKIPPED, "agent", app.match_reasoning)
        skipped += 1
    return skipped


def skip_ineligible_waiting(db: Session, user: User) -> int:
    """Jobs waiting in Swipe Review that your filters now rule out (not an internship for you, a Java role when you
    skip Java, outside your tech focus, a company you avoid...) are skipped, with the reason."""
    waiting = db.scalars(
        select(Application).join(Job, Job.id == Application.job_id).where(*awaiting_swipe(user.id))).all()
    if not waiting:
        return 0
    master = get_master_resume(db, user)
    prefs = with_resume(user.prefs, master.parsed_content if master else None)
    skipped = 0
    for app in waiting:
        fixed, relaxable, _soft = split_reasons(app.job, prefs)  # the hard ones: the same a scan applies
        reasons = fixed if (app.match_details or {}).get("near_miss") else fixed + relaxable
        if reasons:
            app.match_reasoning = reasons[0]
            set_status(db, app, ApplicationStatus.SKIPPED, "agent", reasons[0])
            skipped += 1
    return skipped


# --------------------------------------------------------------------------- your swipes
REVIEWABLE = (ApplicationStatus.DISCOVERED, ApplicationStatus.MATCHED, ApplicationStatus.SKIPPED)


def keep_application(db: Session, user: User, app: Application, decided_by: str = "user",
                     note: str = "Kept in Swipe Review") -> Application:
    """Swipe right: prepare this job (tailor, cover letter, fill) and, with auto_submit_kept, submit it."""
    if app.status not in REVIEWABLE:
        raise ValueError(f"This job is already {app.status.value.replace('_', ' ')}")
    app.review_decision = "keep"
    app.reviewed_at = datetime.now(UTC)
    app.auto_submit = bool(user.prefs.get("auto_submit_kept", True))
    set_status(db, app, ApplicationStatus.PREPARING, decided_by, note)
    dispatch.enqueue("prepare_application", str(app.id), after_commit=db)
    return app


def skip_application(db: Session, app: Application, note: str = "Skipped in Swipe Review") -> Application:
    if app.status not in REVIEWABLE:
        raise ValueError(f"This job is already {app.status.value.replace('_', ' ')}")
    app.review_decision = "skip"
    app.reviewed_at = datetime.now(UTC)
    set_status(db, app, ApplicationStatus.SKIPPED, "user", note)
    return app


def undo_review(db: Session, app: Application) -> Application:
    """Put a swiped job back on the deck (only while nothing has been prepared yet)."""
    if app.review_decision is None:
        raise ValueError("This job has not been swiped")
    if app.review_decision == "skip" and app.status != ApplicationStatus.SKIPPED:
        raise ValueError(f"This job is already {app.status.value.replace('_', ' ')}")
    if app.review_decision == "keep" and (app.status != ApplicationStatus.PREPARING or app.tailored_resume_id):
        raise ValueError("Preparation has already started — withdraw it from Applications instead")
    app.review_decision = None
    app.auto_submit = False
    set_status(db, app, ApplicationStatus.MATCHED, "user", "Swipe undone")
    return app
