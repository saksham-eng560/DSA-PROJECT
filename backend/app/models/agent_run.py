from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONType, UTCDateTime, utcnow


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    run_type: Mapped[str] = mapped_column(String(50), nullable=False)  # scan | apply | email_check | linkedin_sync | prepare
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | completed | failed | cancelled
    trigger: Mapped[str | None] = mapped_column(String(20), default="user")  # user | schedule | system

    jobs_discovered: Mapped[int] = mapped_column(Integer, default=0)
    jobs_matched: Mapped[int] = mapped_column(Integer, default=0)
    applications_prepared: Mapped[int] = mapped_column(Integer, default=0)
    applications_submitted: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    log: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, default=list)
    # Live progress of a scan (phase, percent, per-source status); see services/scan_progress.py
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
