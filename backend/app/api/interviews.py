"""Interviews (Google Calendar sync + AI prep notes)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, parse_uuid
from app.api.serializers import interview_out
from app.models.application import Application
from app.models.enums import ApplicationStatus, InterviewType
from app.models.interview import Interview
from app.schemas.agent import InterviewCreate, InterviewUpdate
from app.services.agent_orchestrator import get_master_resume
from app.services.application_service import set_status
from app.services.calendar_manager import delete_calendar_event, generate_prep, upsert_calendar_event
from app.services.email_parser import meeting_platform

router = APIRouter(prefix="/interviews", tags=["interviews"])


def _get(db: DB, user_id, interview_id: str) -> Interview:  # type: ignore[no-untyped-def]
    interview = db.get(Interview, parse_uuid(interview_id))
    if interview is None or interview.application.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    return interview


def _itype(value: str | None) -> InterviewType | None:
    if not value:
        return None
    try:
        return InterviewType(value)
    except ValueError as exc:
        raise HTTPException(422, "Unknown interview type") from exc


@router.get("")
def list_interviews(user: CurrentUser, db: DB, upcoming: bool | None = None) -> dict:
    query = select(Interview).join(Application, Application.id == Interview.application_id).where(Application.user_id == user.id)
    now = datetime.now(UTC)
    if upcoming is True:
        query = query.where(Interview.scheduled_at >= now).order_by(Interview.scheduled_at.asc())
    elif upcoming is False:
        query = query.where(Interview.scheduled_at < now).order_by(Interview.scheduled_at.desc())
    else:
        query = query.order_by(Interview.scheduled_at.desc())
    return {"items": [interview_out(i) for i in db.scalars(query.limit(200)).all()]}


@router.get("/{interview_id}")
def get_interview(interview_id: str, user: CurrentUser, db: DB) -> dict:
    return interview_out(_get(db, user.id, interview_id))


@router.post("", status_code=201)
def create_interview(body: InterviewCreate, user: CurrentUser, db: DB) -> dict:
    app = db.get(Application, parse_uuid(body.application_id))
    if app is None or app.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    scheduled = body.scheduled_at if body.scheduled_at.tzinfo else body.scheduled_at.replace(tzinfo=UTC)
    interview = Interview(
        application_id=app.id, interview_type=_itype(body.interview_type), scheduled_at=scheduled,
        duration_minutes=body.duration_minutes, timezone=body.timezone, meeting_link=body.meeting_link,
        meeting_platform=meeting_platform(body.meeting_link) or ("onsite" if body.physical_location else None),
        physical_location=body.physical_location, interviewer_names=body.interviewer_names, outcome="pending",
    )
    db.add(interview)
    db.flush()
    master = get_master_resume(db, user)
    prep = generate_prep(master.parsed_content if master else {}, app, interview)
    interview.prep_notes = prep.get("prep_notes")
    interview.company_research = prep.get("company_research")
    interview.likely_questions = prep.get("likely_questions")
    upsert_calendar_event(db, user, app, interview)
    set_status(db, app, ApplicationStatus.INTERVIEW, "user", "Interview added", only_forward=True)
    return interview_out(interview)


@router.patch("/{interview_id}")
def update_interview(interview_id: str, body: InterviewUpdate, user: CurrentUser, db: DB) -> dict:
    interview = _get(db, user.id, interview_id)
    data = body.model_dump(exclude_unset=True)
    if "interview_type" in data:
        data["interview_type"] = _itype(data["interview_type"])
    if data.get("scheduled_at") and data["scheduled_at"].tzinfo is None:
        data["scheduled_at"] = data["scheduled_at"].replace(tzinfo=UTC)
    reschedule = any(k in data for k in ("scheduled_at", "duration_minutes", "meeting_link", "physical_location", "timezone"))
    for key, value in data.items():
        setattr(interview, key, value)
    if "meeting_link" in data:
        interview.meeting_platform = meeting_platform(interview.meeting_link)
    if "scheduled_at" in data:
        interview.reminder_24h_sent = False
        interview.reminder_1h_sent = False
    if reschedule or "prep_notes" in data:
        upsert_calendar_event(db, user, interview.application, interview)
    return interview_out(interview)


@router.post("/{interview_id}/prep")
def regenerate_prep(interview_id: str, user: CurrentUser, db: DB) -> dict:
    interview = _get(db, user.id, interview_id)
    master = get_master_resume(db, user)
    prep = generate_prep(master.parsed_content if master else {}, interview.application, interview)
    interview.prep_notes = prep.get("prep_notes")
    interview.company_research = prep.get("company_research")
    interview.likely_questions = prep.get("likely_questions")
    upsert_calendar_event(db, user, interview.application, interview)
    return interview_out(interview)


@router.delete("/{interview_id}")
def delete_interview(interview_id: str, user: CurrentUser, db: DB) -> dict:
    interview = _get(db, user.id, interview_id)
    delete_calendar_event(db, user, interview)
    db.delete(interview)
    return {"ok": True}
