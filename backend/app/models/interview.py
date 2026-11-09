from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONType, UTCDateTime, utcnow
from app.models.enums import INTERVIEW_TYPE_ENUM, InterviewType

if TYPE_CHECKING:
    from app.models.application import Application


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    communication_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("communications.id", ondelete="SET NULL"))

    google_event_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    google_event_link: Mapped[str | None] = mapped_column(Text)

    interview_type: Mapped[InterviewType | None] = mapped_column(INTERVIEW_TYPE_ENUM)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")

    meeting_link: Mapped[str | None] = mapped_column(Text)
    meeting_platform: Mapped[str | None] = mapped_column(String(50))  # zoom | google_meet | teams | onsite | phone
    physical_location: Mapped[str | None] = mapped_column(Text)

    interviewer_names: Mapped[list[str] | None] = mapped_column(JSONType)
    interviewer_titles: Mapped[list[str] | None] = mapped_column(JSONType)
    interviewer_linkedin_urls: Mapped[list[str] | None] = mapped_column(JSONType)

    prep_notes: Mapped[str | None] = mapped_column(Text)
    company_research: Mapped[str | None] = mapped_column(Text)
    likely_questions: Mapped[list[Any] | None] = mapped_column(JSONType)

    outcome: Mapped[str | None] = mapped_column(String(50))  # passed | failed | pending | rescheduled | cancelled
    feedback: Mapped[str | None] = mapped_column(Text)

    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_1h_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    application: Mapped[Application] = relationship(lazy="joined")
