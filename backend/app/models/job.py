from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, EmbeddingType, JSONType, UTCDateTime, utcnow
from app.models.enums import ATS_PLATFORM_ENUM, EXPERIENCE_LEVEL_ENUM, JOB_TYPE_ENUM, ATSPlatform, ExperienceLevel, JobType


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("idx_jobs_company", "company_name"),
        Index("idx_jobs_platform", "source_platform"),
        Index("idx_jobs_dedupe", "dedupe_key"),
        Index("idx_jobs_company_tier", "company_tier"),
        Index("idx_jobs_company_verdict", "company_verdict"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_logo_url: Mapped[str | None] = mapped_column(Text)
    company_domain: Mapped[str | None] = mapped_column(String(255))
    role_title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    requirements: Mapped[str | None] = mapped_column(Text)
    nice_to_haves: Mapped[str | None] = mapped_column(Text)

    job_type: Mapped[JobType | None] = mapped_column(JOB_TYPE_ENUM)
    experience_level: Mapped[ExperienceLevel | None] = mapped_column(EXPERIENCE_LEVEL_ENUM)
    location: Mapped[str | None] = mapped_column(String(255))
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(10), default="USD")

    source_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    source_platform: Mapped[ATSPlatform] = mapped_column(ATS_PLATFORM_ENUM, nullable=False)
    application_url: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(255))
    easy_apply: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(512))

    description_embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType(1536))
    extracted_skills: Mapped[list[str] | None] = mapped_column(JSONType)
    extracted_requirements: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    # The company check (services/company_verifier.py): verified | unverified | suspicious, the tier of a
    # renowned company (big_tech | product | startup_india | startup_global | ai) and the reasons.
    company_verdict: Mapped[str | None] = mapped_column(String(16))
    company_tier: Mapped[str | None] = mapped_column(String(32))
    company_check: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    posted_date: Mapped[date | None] = mapped_column(Date)
    deadline_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    discovered_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_checked: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
