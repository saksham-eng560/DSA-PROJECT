"""Recruiter communications (Gmail)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, parse_uuid
from app.api.serializers import communication_out
from app.models.application import Application
from app.models.communication import Communication
from app.models.enums import EmailIntent
from app.schemas.agent import CommunicationUpdate, SendReplyRequest
from app.services.gmail_service import create_reply_draft, send_reply
from app.services.google_oauth import GoogleAuthError, GoogleNotConfigured, build_service

router = APIRouter(prefix="/communications", tags=["communications"])


def _get(db: DB, user_id, comm_id: str) -> Communication:  # type: ignore[no-untyped-def]
    comm = db.get(Communication, parse_uuid(comm_id))
    if comm is None or comm.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Communication not found")
    return comm


@router.get("")
def list_communications(
    user: CurrentUser, db: DB, intent: str | None = None, action_required: bool | None = None,
    application_id: str | None = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
) -> dict:
    query = select(Communication).where(Communication.user_id == user.id)
    if intent:
        try:
            query = query.where(Communication.detected_intent == EmailIntent(intent))
        except ValueError as exc:
            raise HTTPException(422, "Unknown intent") from exc
    if action_required is not None:
        query = query.where(Communication.is_action_required.is_(action_required), Communication.action_taken.is_(False))
    if application_id:
        query = query.where(Communication.application_id == parse_uuid(application_id))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(Communication.received_at.desc().nulls_last())
                       .offset((page - 1) * page_size).limit(page_size)).all()
    apps = {a.id: a for a in db.scalars(select(Application).where(
        Application.id.in_([c.application_id for c in items if c.application_id]))).all()} if items else {}
    out = []
    for c in items:
        row = communication_out(c)
        app = apps.get(c.application_id)
        row["application"] = {"id": str(app.id), "company_name": app.job.company_name, "role_title": app.job.role_title,
                              "status": app.status.value} if app else None
        out.append(row)
    return {"items": out, "total": total, "page": page}


@router.get("/{comm_id}")
def get_communication(comm_id: str, user: CurrentUser, db: DB) -> dict:
    return communication_out(_get(db, user.id, comm_id))


@router.patch("/{comm_id}")
def update_communication(comm_id: str, body: CommunicationUpdate, user: CurrentUser, db: DB) -> dict:
    comm = _get(db, user.id, comm_id)
    if body.action_taken is not None:
        comm.action_taken = body.action_taken
    if body.suggested_reply is not None:
        comm.suggested_reply = body.suggested_reply
    if body.application_id is not None:
        if body.application_id == "":
            comm.application_id = None
        else:
            app = db.get(Application, parse_uuid(body.application_id))
            if app is None or app.user_id != user.id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
            comm.application_id = app.id
    return communication_out(comm)


@router.post("/{comm_id}/draft")
def create_draft(comm_id: str, body: SendReplyRequest, user: CurrentUser, db: DB) -> dict:
    comm = _get(db, user.id, comm_id)
    try:
        service = build_service(db, user, "gmail", "v1")
        parsed = {"sender_email": comm.sender_email, "subject": comm.subject, "thread_id": comm.gmail_thread_id,
                  "message_id_header": None}
        comm.gmail_draft_id = create_reply_draft(service, parsed, body.body, user.google_email)
        comm.suggested_reply = body.body
    except (GoogleAuthError, GoogleNotConfigured) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return communication_out(comm)


@router.post("/{comm_id}/send")
def send(comm_id: str, body: SendReplyRequest, user: CurrentUser, db: DB) -> dict:
    """Send a reply from the user's Gmail — only on explicit user action."""
    comm = _get(db, user.id, comm_id)
    try:
        message_id = send_reply(db, user, comm, body.body)
    except (GoogleAuthError, GoogleNotConfigured) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"sent": True, "message_id": message_id}
