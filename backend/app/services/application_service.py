"""Application status transitions with audit trail + real-time updates."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.application import Application, ApplicationStatusHistory
from app.models.enums import RESPONSE_STATUSES, STATUS_RANK, ApplicationStatus
from app.services.notifier import publish_after_commit


def set_status(
    db: Session,
    application: Application,
    new_status: ApplicationStatus,
    changed_by: str = "agent",
    notes: str | None = None,
    only_forward: bool = False,
) -> bool:
    """Change status, record history. Returns False if nothing changed / move rejected."""
    old = application.status
    if old == new_status:
        return False
    if only_forward and STATUS_RANK.get(new_status, 0) < STATUS_RANK.get(old, 0):
        return False
    now = datetime.now(UTC)
    application.status = new_status
    application.updated_at = now
    if new_status == ApplicationStatus.APPLIED and not application.submitted_at:
        application.submitted_at = now
    if new_status == ApplicationStatus.APPROVED:
        application.approved_at = now
        application.submitting_at = None  # a new approval: no attempt to send it has started yet
    if new_status == ApplicationStatus.REJECTED:
        application.rejected_at = now
    if new_status in RESPONSE_STATUSES and not application.first_response_at and application.submitted_at:
        application.first_response_at = now
    db.add(
        ApplicationStatusHistory(
            application_id=application.id, old_status=old, new_status=new_status, changed_by=changed_by, notes=notes
        )
    )
    db.flush()
    publish_after_commit(  # the dashboard refetches on this: it must see the new status
        db,
        str(application.user_id),
        "application_updated",
        {"id": str(application.id), "status": new_status.value, "old_status": old.value if old else None},
    )
    return True
