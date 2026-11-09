"""Interview reminders, weekly summary, data-retention and job-expiry housekeeping."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select

from app.config import settings
from app.core.database import session_scope
from app.core.storage import get_storage
from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.models.interview import Interview
from app.models.job import Job
from app.models.user import Notification, User
from app.services.analytics import compute_overview
from app.services.notifier import notify
from app.services.progress import send_due_digests
from app.worker.celery_app import celery_app
from app.worker.dispatch import register

logger = logging.getLogger(__name__)


@register("send_interview_reminders")
def send_interview_reminders() -> int:
    sent = 0
    now = datetime.now(UTC)
    with session_scope() as db:
        upcoming = db.scalars(
            select(Interview).where(Interview.scheduled_at > now, Interview.scheduled_at < now + timedelta(hours=25),
                                    or_(Interview.outcome == "pending", Interview.outcome.is_(None)))
        ).all()
        for interview in upcoming:
            app = interview.application
            user = db.get(User, app.user_id)
            delta = interview.scheduled_at - now
            where = interview.meeting_link or interview.physical_location or "see calendar"
            if delta <= timedelta(hours=1, minutes=5) and not interview.reminder_1h_sent:
                notify(db, user, "interview_reminder_1h", f"⏰ Interview in 1 hour: {app.job.company_name}",
                       f"{app.job.role_title} — {where}", link=f"/dashboard/interviews?id={interview.id}")
                interview.reminder_1h_sent = True
                interview.reminder_24h_sent = True
                sent += 1
            elif delta <= timedelta(hours=24, minutes=5) and not interview.reminder_24h_sent:
                notify(db, user, "interview_reminder_24h", f"📅 Interview tomorrow: {app.job.company_name}",
                       f"{app.job.role_title} at {interview.scheduled_at.isoformat()} — prep notes are in your dashboard.",
                       link=f"/dashboard/interviews?id={interview.id}")
                interview.reminder_24h_sent = True
                sent += 1
    return sent


@register("progress_digest")
def progress_digest() -> int:
    """Hourly check; each user gets their digest at ~20:00 local time (see services/progress.py)."""
    return send_due_digests()


@register("weekly_summary")
def weekly_summary() -> int:
    count = 0
    with session_scope() as db:
        for user in db.scalars(select(User).where(User.is_active.is_(True))).all():
            stats = compute_overview(db, user, days=7)
            totals = stats["totals"]
            body = (
                f"Last 7 days: {totals['applied']} applications submitted, {totals['pending_approval']} awaiting your review, "
                f"{totals['interviews']} interviews, {totals['offers']} offers. Response rate {stats['rates']['response_rate']}%."
            )
            notify(db, user, "weekly_summary", "Your weekly AutoApply AI summary", body, link="/dashboard/analytics")
            count += 1
    return count


@register("retention_cleanup")
def retention_cleanup() -> int:
    """Delete completed application data older than DATA_RETENTION_DAYS (default 2 years)."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.DATA_RETENTION_DAYS)
    closed = [ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN, ApplicationStatus.SKIPPED,
              ApplicationStatus.ACCEPTED, ApplicationStatus.FAILED]
    removed = 0
    storage = get_storage()
    with session_scope() as db:
        old = db.scalars(select(Application).where(Application.updated_at < cutoff, Application.status.in_(closed))).all()
        for app in old:
            for key in (app.form_screenshot_url, app.confirmation_screenshot_url, app.tailored_resume_pdf_url):
                if key:
                    try:
                        storage.delete(key)
                    except Exception:  # noqa: BLE001
                        pass
            db.delete(app)
            removed += 1
        db.execute(delete(Notification).where(Notification.created_at < datetime.now(UTC) - timedelta(days=90)))
    return removed


@register("expire_stale_jobs")
def expire_stale_jobs() -> int:
    cutoff = datetime.now(UTC) - timedelta(days=45)
    with session_scope() as db:
        jobs = db.scalars(select(Job).where(Job.is_active.is_(True), Job.last_checked < cutoff)).all()
        for job in jobs:
            job.is_active = False
        return len(jobs)


@celery_app.task(name="autoapply.send_interview_reminders")
def send_interview_reminders_task() -> int:
    return send_interview_reminders()


@celery_app.task(name="autoapply.progress_digest")
def progress_digest_task() -> int:
    return progress_digest()


@celery_app.task(name="autoapply.weekly_summary")
def weekly_summary_task() -> int:
    return weekly_summary()


@celery_app.task(name="autoapply.retention_cleanup")
def retention_cleanup_task() -> int:
    return retention_cleanup()


@celery_app.task(name="autoapply.expire_stale_jobs")
def expire_stale_jobs_task() -> int:
    return expire_stale_jobs()
