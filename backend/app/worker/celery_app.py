"""Celery configuration + Celery Beat schedule."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import settings
from app.core.logging_config import configure_logging
from app.worker.dispatch import BROWSER_TASKS

configure_logging()

celery_app = Celery(
    "autoapply",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
    include=[
        "app.worker.tasks_scan",
        "app.worker.tasks_apply",
        "app.worker.tasks_email",
        "app.worker.tasks_sync",
        "app.worker.tasks_calendar",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    # Browser work has its own queue (and its own worker in ./start.sh), so it never holds up a scan
    task_routes={f"autoapply.{name}": {"queue": "browser"} for name in BROWSER_TASKS},
    task_default_queue="default",
    beat_schedule={
        "scan-due-users": {"task": "autoapply.scan_due_users", "schedule": crontab(minute=7)},
        "check-all-emails": {
            "task": "autoapply.check_all_emails",
            "schedule": max(60, settings.EMAIL_POLL_MINUTES * 60),
        },
        "renew-gmail-watches": {"task": "autoapply.renew_gmail_watches", "schedule": crontab(hour=3, minute=17)},
        "interview-reminders": {"task": "autoapply.send_interview_reminders", "schedule": 600.0},
        "linkedin-sync": {"task": "autoapply.linkedin_sync_all", "schedule": crontab(hour=6, minute=23)},
        "weekly-summary": {"task": "autoapply.weekly_summary", "schedule": crontab(day_of_week="mon", hour=8, minute=41)},
        "progress-digest": {"task": "autoapply.progress_digest", "schedule": crontab(minute=43)},  # hourly; ~20:00 local
        "retention-cleanup": {"task": "autoapply.retention_cleanup", "schedule": crontab(hour=4, minute=11)},
        "expire-stale-jobs": {"task": "autoapply.expire_stale_jobs", "schedule": crontab(hour=5, minute=3)},
    },
)

if settings.CELERY_TASK_ALWAYS_EAGER:
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
