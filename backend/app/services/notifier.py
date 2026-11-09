"""Multi-channel notifications (PLAN.md §18): dashboard (DB + WebSocket), e-mail, Discord/Slack."""

from __future__ import annotations

import base64
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.core.websocket import publish_event
from app.models.user import Notification, User

logger = logging.getLogger(__name__)

ALL = frozenset({"dashboard", "email", "chat"})
NOTIFICATION_SUBJECT_PREFIX = "[AutoApply AI]"  # the Gmail monitor ignores these

# Event -> channels, mirroring the matrix in PLAN.md §18.
EVENT_CHANNELS: dict[str, frozenset[str]] = {
    "application_ready": ALL,
    "application_submitted": frozenset({"dashboard", "chat"}),
    "application_failed": ALL,
    "recruiter_email": frozenset({"dashboard", "chat"}),
    "interview_scheduled": ALL,
    "interview_reminder_24h": ALL,
    "interview_reminder_1h": frozenset({"dashboard", "email"}),
    "session_expired": ALL,
    "agent_error": ALL,
    "weekly_summary": frozenset({"email", "chat"}),
    "offer_received": ALL,
    "scan_completed": frozenset({"dashboard"}),
    "linkedin_profile_changed": frozenset({"dashboard", "email"}),
    "status_changed": frozenset({"dashboard"}),
    "self_applied": ALL,
    "progress_digest": frozenset({"dashboard", "email", "chat"}),
}

# Updates about an application you made. With the "progress_updates_everywhere" preference (on by default)
# they reach every channel: dashboard + browser, your Gmail, and Discord/Slack when connected.
PROGRESS_EVENTS = frozenset({"application_submitted", "self_applied", "recruiter_email", "status_changed",
                             "interview_scheduled", "offer_received"})


def _user_channels(user: User) -> set[str]:
    prefs = user.prefs
    return set(prefs.get("notification_channels") or ["dashboard", "email"])


def _absolute(link: str | None) -> str | None:
    if link and link.startswith("/"):
        return settings.FRONTEND_URL.rstrip("/") + link
    return link


def send_email(to: str, subject: str, body: str, db: Session | None = None, user: User | None = None) -> bool:
    """Send via SMTP when configured, else via the user's own Gmail (self-notification)."""
    if settings.SMTP_HOST:
        msg = EmailMessage()
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as smtp:
                if settings.SMTP_STARTTLS:
                    smtp.starttls()
                if settings.SMTP_USER:
                    smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")
                smtp.send_message(msg)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("SMTP send failed: %s", exc)
            return False
    if db is not None and user is not None and user.google_connected:
        from app.services.google_oauth import build_service, has_scope

        if not has_scope(user, "gmail.modify"):
            return False
        try:
            msg = EmailMessage()
            msg["To"] = to
            msg["From"] = user.google_email or to
            msg["Subject"] = subject
            msg.set_content(body)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            build_service(db, user, "gmail", "v1").users().messages().send(userId="me", body={"raw": raw}).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gmail self-notification failed: %s", exc)
    return False


def send_chat(url: str, title: str, body: str, link: str | None) -> bool:
    text = f"**{title}**\n{body}" + (f"\n{link}" if link else "")
    payload: dict[str, Any] = {"content": text[:1900]} if "discord" in url else {"text": text[:3000]}
    try:
        response = httpx.post(url, json=payload, timeout=10)
        return response.status_code < 300
    except httpx.HTTPError as exc:
        logger.warning("Chat webhook failed: %s", exc)
        return False


def notify(
    db: Session,
    user: User,
    event_type: str,
    title: str,
    body: str = "",
    link: str | None = None,
    data: dict[str, Any] | None = None,
) -> Notification | None:
    channels = EVENT_CHANNELS.get(event_type, frozenset({"dashboard"}))
    user_channels = _user_channels(user)
    prefs = user.prefs
    if event_type in PROGRESS_EVENTS and prefs.get("progress_updates_everywhere", True):
        channels = ALL
    notification: Notification | None = None

    if "dashboard" in channels:
        notification = Notification(
            user_id=user.id, event_type=event_type, title=title, body=body, link=link, data=data or {}
        )
        db.add(notification)
        db.flush()
        publish_after_commit(
            db,
            str(user.id),
            "notification",
            {"id": str(notification.id), "event_type": event_type, "title": title, "body": body, "link": link,
             "data": data or {}, "created_at": notification.created_at.isoformat() if notification.created_at else None},
        )

    if "email" in channels and "email" in user_channels:
        send_email(user.email, f"{NOTIFICATION_SUBJECT_PREFIX} {title}", f"{body}\n\n{_absolute(link) or ''}".strip(), db, user)

    if "chat" in channels:
        targets = []
        discord = prefs.get("discord_webhook_url") or settings.DISCORD_WEBHOOK_URL
        slack = prefs.get("slack_webhook_url") or settings.SLACK_WEBHOOK_URL
        if discord and ("discord" in user_channels or prefs.get("discord_webhook_url")):
            targets.append(discord)
        if slack and ("slack" in user_channels or prefs.get("slack_webhook_url")):
            targets.append(slack)
        for url in targets:
            send_chat(url, title, body, _absolute(link))
    return notification


def push_update(user_id: str, event_type: str, data: dict[str, Any]) -> None:
    """Lightweight real-time UI refresh event (no persisted notification)."""
    publish_event(str(user_id), event_type, data)


_PENDING = "pending_events"
_HOOKED = "pending_events_hooked"


def _publish_pending(session: Session) -> None:
    for user_id, event_type, data in session.info.pop(_PENDING, []):
        publish_event(user_id, event_type, data)


def _drop_pending(session: Session) -> None:
    session.info.pop(_PENDING, None)  # those changes were never saved: nothing to tell the dashboard


def publish_after_commit(db: Session, user_id: str, event_type: str, data: dict[str, Any]) -> None:
    """Send a real-time event once ``db`` commits: the dashboard refetches as soon as it hears it, and a
    refetch before the commit would still read the old state (and then wait for the next poll).

    Events wait in ``db.info``; one pair of hooks per session sends them on commit and drops them on a rollback,
    so a scan that changes thousands of applications doesn't keep a listener for each one."""
    if not db.in_transaction():
        publish_event(str(user_id), event_type, data)
        return
    db.info.setdefault(_PENDING, []).append((str(user_id), event_type, data))
    if not db.info.get(_HOOKED):
        event.listen(db, "after_commit", _publish_pending)
        event.listen(db, "after_rollback", _drop_pending)
        db.info[_HOOKED] = True
