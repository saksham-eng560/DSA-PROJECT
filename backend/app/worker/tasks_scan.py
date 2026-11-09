"""Job scanning tasks."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.database import session_scope
from app.models.agent_run import AgentRun
from app.models.user import User
from app.services import agent_orchestrator as orch
from app.worker.celery_app import celery_app
from app.worker.dispatch import enqueue, register

logger = logging.getLogger(__name__)


@register("scan_user")
def scan_user(user_id: str, trigger: str = "user", platforms: list[str] | None = None, run_id: str | None = None) -> str | None:
    with session_scope() as db:
        user = db.get(User, uuid.UUID(user_id))
        if user is None or not user.is_active:
            return None
        existing = db.get(AgentRun, uuid.UUID(run_id)) if run_id else None
        run_log = orch.RunLog(db, user, "scan", trigger, existing=existing)
        run = orch.run_scan(db, user, trigger=trigger, platforms=platforms, run=run_log)
        return str(run.id)


@register("scan_due_users")
def scan_due_users() -> int:
    """Hourly: scan every active user whose scan interval has elapsed."""
    count = 0
    with session_scope() as db:
        users = db.scalars(select(User).where(User.is_active.is_(True))).all()
        now = datetime.now(UTC)
        for user in users:
            prefs = user.prefs
            if not prefs.get("scan_enabled", True):
                continue
            interval = timedelta(hours=int(prefs.get("scan_interval_hours") or 6))
            if user.last_scan_at and now - user.last_scan_at < interval:
                continue
            orch.close_stale_scans(db, user.id)
            running = db.scalar(select(AgentRun.id).where(AgentRun.user_id == user.id, AgentRun.run_type == "scan",
                                                          AgentRun.status == "running",
                                                          AgentRun.started_at > now - timedelta(hours=2)))
            if running:
                continue
            enqueue("scan_user", str(user.id), "schedule")
            count += 1
    return count


@celery_app.task(name="autoapply.scan_user")
def scan_user_task(user_id: str, trigger: str = "user", platforms: list[str] | None = None, run_id: str | None = None) -> str | None:
    return scan_user(user_id, trigger, platforms, run_id)


@celery_app.task(name="autoapply.scan_due_users")
def scan_due_users_task() -> int:
    return scan_due_users()
