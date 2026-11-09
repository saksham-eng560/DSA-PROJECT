from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StartScanRequest(BaseModel):
    platforms: list[str] | None = None


class InterviewCreate(BaseModel):
    application_id: str
    scheduled_at: datetime
    duration_minutes: int = Field(default=60, ge=5, le=600)
    timezone: str = "UTC"
    interview_type: str | None = "video_call"
    meeting_link: str | None = None
    physical_location: str | None = None
    interviewer_names: list[str] | None = None


class InterviewUpdate(BaseModel):
    scheduled_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=600)
    timezone: str | None = None
    interview_type: str | None = None
    meeting_link: str | None = None
    physical_location: str | None = None
    interviewer_names: list[str] | None = None
    outcome: str | None = None
    feedback: str | None = None
    prep_notes: str | None = None


class CommunicationUpdate(BaseModel):
    action_taken: bool | None = None
    application_id: str | None = None
    suggested_reply: str | None = None


class SendReplyRequest(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
