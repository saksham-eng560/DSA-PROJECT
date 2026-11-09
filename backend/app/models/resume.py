from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, EmbeddingType, JSONType, UTCDateTime, utcnow

if TYPE_CHECKING:
    from app.models.user import User


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    label: Mapped[str | None] = mapped_column(String(255))
    original_file_url: Mapped[str | None] = mapped_column(Text)  # storage key of the uploaded file
    original_filename: Mapped[str | None] = mapped_column(String(255))
    raw_text: Mapped[str | None] = mapped_column(Text)

    # Structured content: personal_info, summary, education, experience, projects,
    # skills{technical,languages,tools,soft_skills}, certifications, awards
    parsed_content: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    skills_embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType(1536))

    is_master: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    # Tailored versions point back at the master and the job they target
    parent_resume_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("resumes.id", ondelete="SET NULL"))
    tailored_for_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("jobs.id", ondelete="SET NULL"))
    changes_made: Mapped[list[str] | None] = mapped_column(JSONType)
    pdf_url: Mapped[str | None] = mapped_column(Text)  # storage key of the rendered PDF

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="resumes")
