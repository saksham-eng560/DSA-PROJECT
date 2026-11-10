from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONType, UTCDateTime, utcnow
from app.models.enums import APPLICATION_STATUS_ENUM, ATS_PLATFORM_ENUM, ApplicationStatus, ATSPlatform

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.resume import Resume
    from app.models.user import User


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_application_user_job"),
        Index("idx_applications_user_status", "user_id", "status"),
        # Swipe Review orders a user's deck by score; the undo stack and "kept today" read by review time.
        Index("idx_applications_user_score", "user_id", "match_score"),
        Index("idx_applications_user_reviewed", "user_id", "reviewed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("jobs.id"), nullable=False, index=True)

    status: Mapped[ApplicationStatus] = mapped_column(
        APPLICATION_STATUS_ENUM, default=ApplicationStatus.DISCOVERED, index=True
    )

    # Match analysis
    match_score: Mapped[int | None] = mapped_column(Integer)
    match_reasoning: Mapped[str | None] = mapped_column(Text)
    match_details: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    similarity_score: Mapped[float | None] = mapped_column()

    # Swipe Review: the user's keep / skip decision, and whether a kept job may be submitted
    # without a second review once its form is filled with every required answer.
    review_decision: Mapped[str | None] = mapped_column(String(16))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    auto_submit: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    # Tailored documents
    tailored_resume_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("resumes.id", ondelete="SET NULL"))
    tailored_resume_pdf_url: Mapped[str | None] = mapped_column(Text)
    cover_letter: Mapped[str | None] = mapped_column(Text)

    # ATS-specific answers: [{question, field_type, answer, confidence, needs_user_review}]
    custom_answers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType)
    # Your corrections from the review queue: field key (profile key or form label) -> value
    field_overrides: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    ats_platform: Mapped[ATSPlatform | None] = mapped_column(ATS_PLATFORM_ENUM)
    form_fields: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType)
    needs_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_review_reason: Mapped[str | None] = mapped_column(Text)

    # Submission tracking
    form_screenshot_url: Mapped[str | None] = mapped_column(Text)
    staged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    submitting_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # being sent right now, since (one sender only)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    confirmation_screenshot_url: Mapped[str | None] = mapped_column(Text)
    confirmation_number: Mapped[str | None] = mapped_column(String(255))
    first_response_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Rejection / offer
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    rejected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    offer_details: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    # Errors
    error_log: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="applications")
    job: Mapped[Job] = relationship(lazy="joined")
    tailored_resume: Mapped[Resume | None] = relationship(foreign_keys=[tailored_resume_id])
    history: Mapped[list[ApplicationStatusHistory]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="ApplicationStatusHistory.created_at"
    )


class ApplicationStatusHistory(Base):
    __tablename__ = "application_status_history"
    __table_args__ = (Index("idx_status_history_app_created", "application_id", "created_at"),)  # timeline, in order

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    old_status: Mapped[ApplicationStatus | None] = mapped_column(APPLICATION_STATUS_ENUM)
    new_status: Mapped[ApplicationStatus] = mapped_column(APPLICATION_STATUS_ENUM, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(50), default="agent")  # agent | user | system | email_parser
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    application: Mapped[Application] = relationship(back_populates="history")
