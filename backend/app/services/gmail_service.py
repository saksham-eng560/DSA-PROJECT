"""Gmail monitoring: fetch new mail, classify recruiter e-mails, update applications, label, draft replies."""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage as MimeMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from dateutil import parser as dateparser
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.application import Application
from app.models.communication import Communication
from app.models.enums import (
    STATUS_RANK,
    ApplicationStatus,
    EmailDirection,
    InterviewType,
)
from app.models.interview import Interview
from app.models.resume import Resume
from app.models.user import User
from app.services.application_service import set_status
from app.services.calendar_manager import generate_prep, upsert_calendar_event
from app.services.email_parser import EmailMessage, analyze_email, email_intent_enum, is_job_related, meeting_platform
from app.services.google_oauth import build_service, has_scope
from app.services.notifier import NOTIFICATION_SUBJECT_PREFIX, notify
from app.services.text_utils import html_to_text

logger = logging.getLogger(__name__)

LABEL_ROOT = "AutoApply AI"
INTENT_LABELS = {
    "acknowledgment": "Acknowledged",
    "rejection": "Rejected",
    "interview_invite": "Interview",
    "assessment": "Assessment",
    "offer": "Offer",
    "follow_up": "Follow-up",
    "info_request": "Action Needed",
    "generic": "Job Related",
}
AUTO_DRAFT_INTENTS = {"interview_invite", "assessment", "offer", "info_request"}


# --------------------------------------------------------------------------- MIME helpers
def _decode(data: str | None) -> str:
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _walk(payload: dict[str, Any], plain: list[str], html: list[str], attachments: list[dict[str, Any]]) -> None:
    mime = payload.get("mimeType", "")
    body = payload.get("body") or {}
    if payload.get("filename"):
        attachments.append({"filename": payload["filename"], "mime_type": mime, "attachment_id": body.get("attachmentId")})
    elif mime == "text/plain":
        plain.append(_decode(body.get("data")))
    elif mime == "text/html":
        html.append(_decode(body.get("data")))
    for part in payload.get("parts") or []:
        _walk(part, plain, html, attachments)


def parse_gmail_message(raw: dict[str, Any]) -> dict[str, Any]:
    payload = raw.get("payload") or {}
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers") or []}
    plain: list[str] = []
    html: list[str] = []
    attachments: list[dict[str, Any]] = []
    _walk(payload, plain, html, attachments)
    body_html = "\n".join(html)
    body_text = "\n".join(p for p in plain if p.strip()) or html_to_text(body_html)
    sender_name, sender_email = parseaddr(headers.get("from", ""))
    _, recipient = parseaddr(headers.get("to", ""))
    received = None
    if raw.get("internalDate"):
        received = datetime.fromtimestamp(int(raw["internalDate"]) / 1000, tz=UTC)
    elif headers.get("date"):
        try:
            received = parsedate_to_datetime(headers["date"])
        except (TypeError, ValueError):
            received = None
    return {
        "id": raw.get("id"),
        "thread_id": raw.get("threadId"),
        "label_ids": raw.get("labelIds") or [],
        "history_id": raw.get("historyId"),
        "subject": headers.get("subject", ""),
        "sender_name": sender_name,
        "sender_email": sender_email.lower(),
        "recipient": recipient,
        "message_id_header": headers.get("message-id"),
        "body_text": body_text,
        "body_html": body_html,
        "attachments": attachments,
        "received_at": received,
    }


# --------------------------------------------------------------------------- labels / drafts
def _ensure_label(service: Any, name: str, cache: dict[str, str]) -> str | None:
    if name in cache:
        return cache[name]
    try:
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
        for label in labels:
            cache[label["name"]] = label["id"]
        if name not in cache:
            created = service.users().labels().create(
                userId="me", body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
            ).execute()
            cache[name] = created["id"]
        return cache[name]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not create Gmail label %s: %s", name, exc)
        return None


def create_reply_draft(service: Any, parsed: dict[str, Any], reply_text: str, from_email: str | None) -> str | None:
    msg = MimeMessage()
    msg["To"] = parsed["sender_email"]
    if from_email:
        msg["From"] = from_email
    subject = parsed["subject"] or ""
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if parsed.get("message_id_header"):
        msg["In-Reply-To"] = parsed["message_id_header"]
        msg["References"] = parsed["message_id_header"]
    msg.set_content(reply_text)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = service.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": parsed.get("thread_id")}}
    ).execute()
    return draft.get("id")


def send_reply(db: Session, user: User, communication: Communication, text: str) -> str:
    service = build_service(db, user, "gmail", "v1")
    msg = MimeMessage()
    msg["To"] = communication.sender_email or ""
    if user.google_email:
        msg["From"] = user.google_email
    subject = communication.subject or ""
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg.set_content(text)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = service.users().messages().send(
        userId="me", body={"raw": raw, "threadId": communication.gmail_thread_id}
    ).execute()
    db.add(
        Communication(
            user_id=user.id,
            application_id=communication.application_id,
            gmail_message_id=sent.get("id"),
            gmail_thread_id=communication.gmail_thread_id,
            direction=EmailDirection.OUTBOUND,
            sender_email=user.google_email or user.email,
            sender_name=user.full_name,
            recipient_email=communication.sender_email,
            subject=msg["Subject"],
            body_text=text,
            received_at=datetime.now(UTC),
        )
    )
    communication.action_taken = True
    return sent.get("id", "")


# --------------------------------------------------------------------------- sync
def _list_new_message_ids(service: Any, user: User) -> tuple[list[str], str | None]:
    ids: list[str] = []
    latest_history = user.gmail_history_id
    if user.gmail_history_id:
        try:
            page_token = None
            while True:
                resp = service.users().history().list(
                    userId="me", startHistoryId=user.gmail_history_id, historyTypes=["messageAdded"],
                    labelId="INBOX", pageToken=page_token,
                ).execute()
                for record in resp.get("history", []):
                    for added in record.get("messagesAdded", []):
                        ids.append(added["message"]["id"])
                latest_history = resp.get("historyId", latest_history)
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            return list(dict.fromkeys(ids)), latest_history
        except Exception as exc:  # noqa: BLE001 - history expired (404) -> fall back to search
            logger.info("Gmail history sync unavailable (%s); falling back to search", exc)
    query = f"newer_than:{settings.GMAIL_LOOKBACK_DAYS}d in:inbox -category:promotions -category:social"
    resp = service.users().messages().list(userId="me", q=query, maxResults=100).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    profile = service.users().getProfile(userId="me").execute()
    return ids, profile.get("historyId")


def _app_summaries(db: Session, user: User) -> list[dict[str, Any]]:
    apps = db.scalars(
        select(Application).where(
            Application.user_id == user.id,
            Application.status.notin_([ApplicationStatus.DISCOVERED, ApplicationStatus.SKIPPED]),
        )
    ).all()
    return [
        {"id": str(a.id), "company_name": a.job.company_name, "role_title": a.job.role_title, "status": a.status.value}
        for a in apps
    ]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = dateparser.isoparse(value)
    except (ValueError, TypeError):
        try:
            parsed = dateparser.parse(value)
        except (ValueError, TypeError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def process_message(
    db: Session,
    user: User,
    parsed: dict[str, Any],
    applications: list[dict[str, Any]],
    service: Any | None = None,
    label_cache: dict[str, str] | None = None,
) -> Communication | None:
    """Analyze one inbound e-mail and apply all side effects. Returns the stored Communication."""
    if parsed.get("id") and db.scalar(select(Communication.id).where(Communication.gmail_message_id == parsed["id"])):
        return None
    msg = EmailMessage(
        sender_email=parsed["sender_email"],
        sender_name=parsed.get("sender_name") or "",
        subject=parsed.get("subject") or "",
        body=parsed.get("body_text") or "",
        received_at=parsed.get("received_at"),
    )
    if user.google_email and msg.sender_email == user.google_email.lower():
        return None
    if msg.subject.startswith(NOTIFICATION_SUBJECT_PREFIX):  # our own notification e-mails (e.g. sent via SMTP)
        return None
    if not is_job_related(msg, [a["company_name"] for a in applications]):
        return None

    prefs = user.prefs
    analysis = analyze_email(msg, applications, user.full_name, prefs.get("timezone") or "UTC")
    intent = analysis["intent"]
    details = analysis["extracted_details"]
    app_id = analysis.get("matched_application_id") or None
    try:
        application = db.get(Application, uuid.UUID(str(app_id))) if app_id else None
    except ValueError:
        application = None
    if application is not None and application.user_id != user.id:
        application = None

    comm = Communication(
        user_id=user.id,
        application_id=application.id if application else None,
        gmail_message_id=parsed.get("id"),
        gmail_thread_id=parsed.get("thread_id"),
        gmail_label_ids=parsed.get("label_ids"),
        direction=EmailDirection.INBOUND,
        sender_email=msg.sender_email,
        sender_name=msg.sender_name,
        recipient_email=parsed.get("recipient"),
        subject=msg.subject,
        body_text=msg.body[:50000],
        body_html=(parsed.get("body_html") or "")[:100000] or None,
        attachments=parsed.get("attachments") or None,
        detected_intent=email_intent_enum(intent),
        intent_confidence=analysis.get("confidence"),
        urgency=analysis.get("urgency"),
        extracted_details=details,
        suggested_reply=analysis.get("suggested_reply") or None,
        is_action_required=intent in AUTO_DRAFT_INTENTS,
        received_at=msg.received_at,
    )
    db.add(comm)
    db.flush()

    # Application status update (never moves backwards)
    progress = ""
    if application is not None and analysis.get("status_update") not in (None, "", "none"):
        try:
            new_status = ApplicationStatus(analysis["status_update"])
        except ValueError:
            new_status = None
        if new_status and STATUS_RANK.get(application.status, 0) >= STATUS_RANK[ApplicationStatus.APPROVED] - 1:
            before = application.status
            changed = set_status(db, application, new_status, changed_by="email_parser",
                                 notes=f"Detected '{intent}' e-mail: {msg.subject[:120]}", only_forward=True)
            if changed:
                progress = (f"\nApplication status: {before.value.replace('_', ' ')} → {new_status.value.replace('_', ' ')} "
                            f"({application.job.role_title} @ {application.job.company_name})")
            if changed and new_status == ApplicationStatus.REJECTED:
                application.rejection_reason = details.get("next_steps") or msg.subject[:250]
            if changed and new_status == ApplicationStatus.OFFER:
                application.offer_details = {"email_subject": msg.subject, "received_at": str(msg.received_at)}

    # Interview scheduling
    interview_at = _parse_iso(details.get("interview_date"))
    if application is not None and intent == "interview_invite" and interview_at:
        existing = db.scalar(
            select(Interview).where(Interview.application_id == application.id, Interview.scheduled_at == interview_at)
        )
        if existing is None:
            itype = details.get("interview_type") or ""
            try:
                interview_type = InterviewType(itype) if itype else InterviewType.OTHER
            except ValueError:
                interview_type = InterviewType.OTHER
            link = details.get("meeting_link") or None
            interview = Interview(
                application_id=application.id,
                communication_id=comm.id,
                interview_type=interview_type,
                scheduled_at=interview_at,
                duration_minutes=int(details.get("duration_minutes") or 60) or 60,
                timezone=prefs.get("timezone") or "UTC",
                meeting_link=link,
                meeting_platform=meeting_platform(link) or ("phone" if interview_type == InterviewType.PHONE_SCREEN else None),
                interviewer_names=[details["interviewer_name"]] if details.get("interviewer_name") else None,
                outcome="pending",
            )
            db.add(interview)
            db.flush()
            master = db.scalar(select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True)))
            prep = generate_prep(master.parsed_content if master else {}, application, interview)
            interview.prep_notes = prep.get("prep_notes")
            interview.company_research = prep.get("company_research")
            interview.likely_questions = prep.get("likely_questions")
            upsert_calendar_event(db, user, application, interview)
            notify(
                db, user, "interview_scheduled",
                f"Interview scheduled: {application.job.role_title} @ {application.job.company_name}",
                f"{interview_at.strftime('%a %b %d, %H:%M %Z')} — prep notes are ready.",
                link=f"/dashboard/interviews?id={interview.id}",
                data={"interview_id": str(interview.id), "application_id": str(application.id)},
            )

    # Gmail label + draft
    if service is not None:
        cache = label_cache if label_cache is not None else {}
        root = _ensure_label(service, LABEL_ROOT, cache)
        label_id = _ensure_label(service, f"{LABEL_ROOT}/{INTENT_LABELS.get(intent, 'Job Related')}", cache)
        add = [lid for lid in (root, label_id) if lid]
        if add and parsed.get("id"):
            try:
                service.users().messages().modify(userId="me", id=parsed["id"], body={"addLabelIds": add}).execute()
                comm.gmail_label_ids = sorted(set((comm.gmail_label_ids or []) + add))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to label message: %s", exc)
        if comm.suggested_reply and intent in AUTO_DRAFT_INTENTS and prefs.get("auto_draft_replies", True):
            try:
                comm.gmail_draft_id = create_reply_draft(service, parsed, comm.suggested_reply, user.google_email)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to create draft reply: %s", exc)

    event = "offer_received" if intent == "offer" else "recruiter_email"
    company = application.job.company_name if application else (analysis.get("company_name") or msg.sender_name or msg.sender_email)
    notify(
        db, user, event,
        f"{'🎉 Offer from' if intent == 'offer' else 'Recruiter e-mail from'} {company}",
        f"{intent.replace('_', ' ').title()}: {msg.subject}{progress}",
        link=f"/dashboard/emails?id={comm.id}",
        data={"communication_id": str(comm.id), "intent": intent},
    )
    return comm


def sync_user_inbox(db: Session, user: User, max_messages: int = 60) -> dict[str, int]:
    stats = {"fetched": 0, "processed": 0, "skipped": 0}
    if not user.google_connected or not has_scope(user, "gmail"):
        return stats
    service = build_service(db, user, "gmail", "v1")
    ids, latest_history = _list_new_message_ids(service, user)
    known = set(db.scalars(select(Communication.gmail_message_id).where(Communication.gmail_message_id.in_(ids))).all()) if ids else set()
    applications = _app_summaries(db, user)
    label_cache: dict[str, str] = {}
    for message_id in [i for i in ids if i not in known][:max_messages]:
        try:
            raw = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch Gmail message %s: %s", message_id, exc)
            continue
        stats["fetched"] += 1
        parsed = parse_gmail_message(raw)
        if "SENT" in parsed["label_ids"] or "DRAFT" in parsed["label_ids"]:
            stats["skipped"] += 1
            continue
        try:
            with db.begin_nested():
                result = process_message(db, user, parsed, applications, service, label_cache)
        except Exception:
            logger.exception("Failed to process Gmail message %s", message_id)
            result = None
        stats["processed" if result else "skipped"] += 1
    if latest_history:
        user.gmail_history_id = str(latest_history)
    user.gmail_last_polled_at = datetime.now(UTC)
    return stats


def start_watch(db: Session, user: User) -> dict[str, Any] | None:
    """Register Gmail push notifications to the configured Pub/Sub topic (renew every < 7 days)."""
    if not settings.GMAIL_PUBSUB_TOPIC or not user.google_connected:
        return None
    service = build_service(db, user, "gmail", "v1")
    resp = service.users().watch(
        userId="me", body={"topicName": settings.GMAIL_PUBSUB_TOPIC, "labelIds": ["INBOX"], "labelFilterBehavior": "include"}
    ).execute()
    if not user.gmail_history_id:
        user.gmail_history_id = str(resp.get("historyId"))
    if resp.get("expiration"):
        user.gmail_watch_expiration = datetime.fromtimestamp(int(resp["expiration"]) / 1000, tz=UTC)
    return resp


def watch_needs_renewal(user: User) -> bool:
    if not settings.GMAIL_PUBSUB_TOPIC:
        return False
    exp = user.gmail_watch_expiration
    return exp is None or exp - datetime.now(UTC) < timedelta(days=2)
