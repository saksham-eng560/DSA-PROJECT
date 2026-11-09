"""Agent runs: every scan, preparation and submission is logged to ``agent_runs`` (PLAN.md §8 rule #6), and runs
the app was stopped in the middle of are closed (and their work queued again) instead of hanging forever."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.models.user import User
from app.services.notifier import publish_after_commit
from app.worker import dispatch

logger = logging.getLogger(__name__)


class RunLog:
    def __init__(self, db: Session, user: User, run_type: str, trigger: str = "user", existing: AgentRun | None = None) -> None:
        self.db = db
        if existing is not None:
            self.run = existing
            self.run.status = "running"
        else:
            self.run = AgentRun(user_id=user.id, run_type=run_type, trigger=trigger, status="running", log=[])
            db.add(self.run)
        db.flush()
        self._t0 = time.monotonic()

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        entry = {"ts": datetime.now(UTC).isoformat(), "level": level, "message": message}
        if data:
            entry["data"] = data
        self.run.log = [*(self.run.log or []), entry][-500:]
        if level == "error":
            self.run.errors_count = (self.run.errors_count or 0) + 1
        getattr(logger, "warning" if level == "error" else "info")("[run %s] %s", self.run.id, message)

    def finish(self, status: str = "completed") -> AgentRun:
        self.run.status = status
        self.run.completed_at = datetime.now(UTC)
        self.run.duration_seconds = int(time.monotonic() - self._t0)
        self.db.flush()
        publish_after_commit(self.db, str(self.run.user_id), "agent_run_updated", {"id": str(self.run.id), "status": status})
        return self.run


PREPARED = "prepared_at"  # in match_details: when the resume, cover letter and answers were all ready


def is_prepared(app: Application) -> bool:
    """True once preparation finished; the resume alone is saved before the (slow) cover letter is written."""
    return bool((app.match_details or {}).get(PREPARED))


STALE_SCAN_MINUTES = 20  # a live scan reports progress every few seconds; this long without any is a dead one


def close_stale_scans(db: Session, user_id: Any) -> int:
    """Scans left "running" by an app that was stopped mid-scan (Ctrl-C, a closed laptop) are marked failed, so
    the dashboard doesn't show a frozen progress bar with a clock counting from weeks ago."""
    now = datetime.now(UTC)
    closed = 0
    for run in db.scalars(select(AgentRun).where(AgentRun.user_id == user_id, AgentRun.run_type == "scan",
                                                 AgentRun.status == "running")).all():
        last = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=UTC)
        try:
            updated = datetime.fromisoformat(str((run.progress or {}).get("updated_at")))
            last = max(last, updated if updated.tzinfo else updated.replace(tzinfo=UTC))
        except (TypeError, ValueError):
            pass
        if now - last < timedelta(minutes=STALE_SCAN_MINUTES):
            continue
        run.status = "failed"
        run.completed_at = now
        run.log = [*(run.log or []), {"ts": now.isoformat(), "level": "warning",
                                      "message": "Interrupted: the app stopped during this scan. Run a new scan."}]
        if run.progress:
            run.progress = {**run.progress, "phase": "failed", "message": "Interrupted (the app was stopped)"}
        closed += 1
    if closed:
        db.flush()
    return closed


def resume_interrupted_work(db: Session) -> int:
    """Without Redis, queued work lives in the app's memory: jobs you kept (or approved) while the app was
    stopped would sit at "preparing" / "approved" forever. Called once at start-up in that mode (when no task
    of this app can be running), it queues them again; submission still only ever sends an approved one, and only
    once, even when another process is sending it right now (``submission._claim_submission``)."""
    now = datetime.now(UTC)
    for run in db.scalars(select(AgentRun).where(AgentRun.run_type.in_(("prepare", "apply")),
                                                 AgentRun.status == "running")).all():
        run.status, run.completed_at = "failed", now
        run.log = [*(run.log or []), {"ts": now.isoformat(), "level": "warning",
                                      "message": "Interrupted: the app stopped; it was picked up again on start-up"}]
    apps = db.scalars(select(Application).where(
        Application.status.in_((ApplicationStatus.PREPARING, ApplicationStatus.APPROVED)))).all()
    for app in apps:
        if app.status == ApplicationStatus.APPROVED:
            task = "submit_application"
        elif is_prepared(app):  # documents ready: it was filling the form
            task = "stage_application"
        # else stopped mid-way (e.g. while the cover letter was written): prepare again, which is harmless
        else:
            task = "prepare_application"
        dispatch.enqueue(task, str(app.id), after_commit=db)
    if apps:
        logger.info("Resumed %s application(s) the app was stopped in the middle of", len(apps))
    return len(apps)
