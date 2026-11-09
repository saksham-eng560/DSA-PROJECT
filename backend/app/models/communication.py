from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONType, UTCDateTime, utcnow
from app.models.enums import EMAIL_DIRECTION_ENUM, EMAIL_INTENT_ENUM, EmailDirection, EmailIntent


class Communication(Base):
    __tablename__ = "communications"
    __table_args__ = (Index("idx_comms_action", "is_action_required"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="SET NULL"), index=True
    )

    gmail_message_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(255), index=True)
    gmail_label_ids: Mapped[list[str] | None] = mapped_column(JSONType)

    direction: Mapped[EmailDirection] = mapped_column(EMAIL_DIRECTION_ENUM, nullable=False)
    sender_email: Mapped[str | None] = mapped_column(String(255))
    sender_name: Mapped[str | None] = mapped_column(String(255))
    recipient_email: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType)

    detected_intent: Mapped[EmailIntent | None] = mapped_column(EMAIL_INTENT_ENUM)
    intent_confidence: Mapped[float | None] = mapped_column(Float)
    urgency: Mapped[str | None] = mapped_column(String(16))
    extracted_details: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    suggested_reply: Mapped[str | None] = mapped_column(Text)
    gmail_draft_id: Mapped[str | None] = mapped_column(String(255))

    is_action_required: Mapped[bool] = mapped_column(Boolean, default=False)
    action_taken: Mapped[bool] = mapped_column(Boolean, default=False)

    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
