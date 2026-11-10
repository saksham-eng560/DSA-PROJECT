"""Application status transitions with audit trail + real-time updates."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.application import Application, ApplicationStatusHistory
from app.models.enums import RESPONSE_STATUSES, STATUS_RANK, ApplicationStatus
from app.services import state_machine
from app.services.notifier import publish_after_commit

logger = logging.getLogger(__name__)


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
    if not state_machine.is_expected(old, new_status):
        # Not an edge of the lifecycle graph (a manual edit, an out-of-order e-mail): allowed, but worth a trace.
        logger.info("Unusual status change %s -> %s for application %s (by %s)",
                    old.value if old else None, new_status.value, application.id, changed_by)
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
