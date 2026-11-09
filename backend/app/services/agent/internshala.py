"""The opt-in Internshala bot. Internshala only takes applications from your own logged-in account, so the bot
applies with the login the Chrome extension syncs. Everything that is different about it lives here: when it's
ready (and what it still needs), its daily limit, one browser at a time on your account, and what happens when
Internshala refuses the synced login. The rest of the pipeline treats an Internshala posting like any other."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.automation import browser
from app.models.application import Application
from app.models.application import ApplicationStatusHistory as History
from app.models.enums import ApplicationStatus
from app.models.job import Job
from app.models.user import User
from app.services.agent.jobs import suspicious_message
from app.services.application_service import set_status
from app.services.company_verifier import SUSPICIOUS, is_trusted
from app.services.notifier import notify
from app.submitters.base import SubmissionResult
from app.submitters.internshala_apply import EXPIRED
from app.worker import dispatch

INTERNSHALA_RECHECK_SECONDS = 50 * 60  # over the daily limit: look again later (it goes out the next day)

INTERNSHALA_MISSING = {
    "bot_off": "The Internshala bot is off: turn on “Let the agent apply on Internshala” in Settings › Integrations "
               "— or apply yourself and click “I Applied”.",
    "not_synced": "The Internshala bot is on, but your Internshala login isn't synced yet: log into internshala.com in "
                  "Chrome, open the AutoApply extension and click “Sync Internshala session” — or apply yourself and "
                  "click “I Applied”.",
    "expired": "Your synced Internshala login has expired: log into internshala.com in Chrome again and click “Sync "
               "Internshala session” in the extension — or apply yourself and click “I Applied”.",
}

INTERNSHALA_FILLING = ("The bot hasn't filled this Internshala form yet: click “Apply with the bot” and it fills the "
                      "form and submits it for you — or apply yourself and click “I Applied”.")


def is_internshala_job(job: Job) -> bool:
    return ((job.raw_data or {}).get("listing_source") == "internshala"
            or "internshala.com/" in (job.application_url or job.source_url or ""))


def internshala_ready(user: User) -> bool:
    """The opt-in Internshala bot is on and holds a working Internshala login."""
    return bool(user.prefs.get("internshala_bot_enabled") and user.internshala_session and user.internshala_session_valid)


def internshala_missing(user: User) -> str | None:
    """What the Internshala bot still needs: "bot_off", "not_synced", "expired" — or None when ready."""
    if not user.prefs.get("internshala_bot_enabled"):
        return "bot_off"
    if not user.internshala_session:
        return "not_synced"
    if not user.internshala_session_valid:
        return "expired"
    return None


def bot_apply_requested(app: Application) -> bool:
    return bool((app.match_details or {}).get("bot_apply_requested_at"))


def internshala_auto_submit_allowed(user: User, app: Application) -> bool:
    """With the bot ready, a kept Internshala job is sent on its own when "Submit automatically" is on or you
    clicked "Apply with the bot" on it."""
    return internshala_ready(user) and bool(user.prefs.get("internshala_auto_submit") or bot_apply_requested(app))


def internshala_blocker(user: User, app: Application) -> str | None:
    """Why the bot can't submit this Internshala application yet (None = it can)."""
    missing = internshala_missing(user)
    if missing:
        return INTERNSHALA_MISSING[missing]
    if not app.form_fields:  # prepared while the bot was off: the real form hasn't been read yet
        return INTERNSHALA_FILLING
    return None


# --------------------------------------------------------------------------- the daily limit
def internshala_submitted_today(db: Session, user: User) -> int:
    """Internshala applications the agent sent today (UTC), for ``internshala_daily_limit``."""
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(db.scalar(
        select(func.count()).select_from(History)
        .join(Application, Application.id == History.application_id).join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user.id, History.new_status == ApplicationStatus.APPLIED,
               History.changed_by == "agent", History.created_at >= start,
               or_(Job.source_url.like("%internshala.com/%"), Job.application_url.like("%internshala.com/%")))
    ) or 0)


def internshala_over_limit(db: Session, user: User) -> str | None:
    limit = max(1, min(25, int(user.prefs.get("internshala_daily_limit") or 15)))
    done = internshala_submitted_today(db, user)
    return f"Daily Internshala limit reached ({done}/{limit}); it goes out tomorrow" if done >= limit else None


# --------------------------------------------------------------------------- your Internshala account
# One browser at a time on your Internshala account (a person never applies from four windows at once), and
# once Internshala has refused the synced login, nothing else tries it until you sync again. Per process: the
# local app runs every job in one process.
_guard = threading.Lock()
_locks: dict[uuid.UUID, threading.Lock] = {}
_refused: dict[uuid.UUID, float] = {}   # when the bot last saw Internshala refuse the login
_notified: dict[uuid.UUID, float] = {}  # the sync we already told you about


def _synced_at(user: User) -> float:
    return user.internshala_session_updated_at.timestamp() if user.internshala_session_updated_at else 0.0


def _lock(user_id: uuid.UUID) -> threading.Lock:
    with _guard:
        return _locks.setdefault(user_id, threading.Lock())


def internshala_refused(user: User) -> bool:
    """Internshala refused the login you synced most recently (your next sync clears this)."""
    return _refused.get(user.id, -1.0) >= _synced_at(user)


def run_on_internshala_account(user: User, drive: Callable[[], SubmissionResult]) -> SubmissionResult:
    """Run ``drive`` (a browser on your Internshala account) the way a person would: one at a time, never again
    with a login Internshala already refused, and keeping the renewed login Internshala hands back."""
    with _lock(user.id):  # queued runs wait here, then see what the run before them found
        if internshala_refused(user):
            return SubmissionResult(False, "failed", error=EXPIRED, session_expired=True)  # no browser opened
        result = drive()
        if result.session_expired:
            _refused[user.id] = time.time()
        elif result.session_cookies:  # Internshala renewed your login while the bot used it: keep the fresh copy
            user.internshala_session = result.session_cookies
        return result


def internshala_session_expired(db: Session, user: User) -> None:
    user.internshala_session_valid = False
    if _notified.get(user.id) == _synced_at(user):
        return  # one notification per synced login, not one per waiting application
    _notified[user.id] = _synced_at(user)
    notify(db, user, "session_expired", "Internshala session expired",
           "Internshala showed the bot its sign-up / login page, so the synced login no longer works there. Log into "
           "internshala.com in Chrome (log out and back in if you already are), then click “Sync Internshala "
           "session” in the extension: the waiting applications are filled again automatically.",
           link="/dashboard/settings?tab=integrations")


# --------------------------------------------------------------------------- your clicks
def bot_apply(db: Session, user: User, app: Application) -> Application:
    """Your "Apply with the bot" click: the bot fills this Internshala form and submits it by itself.

    The click is your approval for this one application. If a required question has no answer the
    bot can stand behind, it stops and the application comes back to Ready to submit with the reason.
    """
    if not is_internshala_job(app.job):
        raise ValueError("“Apply with the bot” is for Internshala postings; use Submit for this one")
    if app.job.company_verdict == SUSPICIOUS and not is_trusted(app.job, user.prefs):
        raise ValueError(suspicious_message(app.job))
    missing = internshala_missing(user)
    if missing:
        raise ValueError(INTERNSHALA_MISSING[missing])
    problem = browser.browser_problem()
    if problem:
        raise ValueError(problem)
    if app.status not in (ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.FAILED, ApplicationStatus.MATCHED,
                          ApplicationStatus.DISCOVERED):
        raise ValueError(f"This application is {app.status.value.replace('_', ' ')}")
    app.match_details = {**(app.match_details or {}), "bot_apply_requested_at": datetime.now(UTC).isoformat()}
    app.review_decision = "keep"
    app.auto_submit = True
    app.needs_manual_review = False
    app.manual_review_reason = None
    app.retry_count = 0
    set_status(db, app, ApplicationStatus.PREPARING, "user", "You asked the bot to fill and submit this on Internshala")
    dispatch.enqueue("stage_application", str(app.id), after_commit=db)
    return app


def restage_internshala_waiting(db: Session, user: User) -> int:
    """Internshala applications prepared while the bot was off get their real form filled once it's on,
    so you review what will actually be sent (never submit a form nobody has seen)."""
    if not internshala_ready(user):
        return 0
    waiting = db.scalars(select(Application).where(Application.user_id == user.id,
                                                   Application.status == ApplicationStatus.PENDING_APPROVAL)).all()
    count = 0
    for app in waiting:
        if is_internshala_job(app.job) and not app.form_fields:
            app.needs_manual_review = False
            app.manual_review_reason = None
            if app.review_decision == "keep":  # prepared while the bot couldn't apply: your keep counts again
                app.auto_submit = bool(user.prefs.get("auto_submit_kept", True))
            set_status(db, app, ApplicationStatus.PREPARING, "agent", "Filling the Internshala form with the bot")
            dispatch.enqueue("stage_application", str(app.id), after_commit=db)
            count += 1
    return count
