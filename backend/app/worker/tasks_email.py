"""Gmail monitoring tasks (polling fallback every EMAIL_POLL_MINUTES + Pub/Sub push handling)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select

from app.core.database import session_scope
from app.models.user import User
from app.services.gmail_service import start_watch, sync_user_inbox, watch_needs_renewal
from app.services.google_oauth import GoogleAuthError
from app.services.notifier import notify
from app.worker.celery_app import celery_app
from app.worker.dispatch import enqueue, register

logger = logging.getLogger(__name__)


@register("check_user_email")
def check_user_email(user_id: str) -> dict[str, int]:
    with session_scope() as db:
        user = db.get(User, uuid.UUID(user_id))
        if user is None or not user.google_connected:
            return {}
        try:
            return sync_user_inbox(db, user)
        except GoogleAuthError as exc:
            notify(db, user, "session_expired", "Google access expired",
                   f"Reconnect Google in Settings to keep monitoring recruiter e-mails. ({exc})", link="/dashboard/settings")
            return {}


@register("check_all_emails")
def check_all_emails() -> int:
    with session_scope() as db:
        ids = [str(u) for u in db.scalars(select(User.id).where(User.is_active.is_(True),
                                                                   User.google_refresh_token.is_not(None))).all()]
    for uid in ids:
        enqueue("check_user_email", uid)
    return len(ids)


@register("handle_gmail_push")
def handle_gmail_push(email_address: str, history_id: str | None = None) -> None:
    with session_scope() as db:
        user = db.scalar(select(User).where(func.lower(User.google_email) == email_address.lower()))
        if user is None:
            user = db.scalar(select(User).where(func.lower(User.email) == email_address.lower()))
        if user is not None:
            enqueue("check_user_email", str(user.id))


@register("renew_gmail_watches")
def renew_gmail_watches() -> int:
    renewed = 0
    with session_scope() as db:
        for user in db.scalars(select(User).where(User.google_refresh_token.is_not(None))).all():
            if watch_needs_renewal(user):
                try:
                    if start_watch(db, user):
                        renewed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Gmail watch renewal failed for %s: %s", user.id, exc)
    return renewed


@celery_app.task(name="autoapply.check_user_email")
def check_user_email_task(user_id: str) -> dict[str, int]:
    return check_user_email(user_id)


@celery_app.task(name="autoapply.check_all_emails")
def check_all_emails_task() -> int:
    return check_all_emails()


@celery_app.task(name="autoapply.handle_gmail_push")
def handle_gmail_push_task(email_address: str, history_id: str | None = None) -> None:
    handle_gmail_push(email_address, history_id)


@celery_app.task(name="autoapply.renew_gmail_watches")
def renew_gmail_watches_task() -> int:
    return renew_gmail_watches()
