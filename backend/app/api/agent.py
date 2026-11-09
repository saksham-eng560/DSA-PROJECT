"""Agent control: start scans, inspect runs (audit log), trigger e-mail checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, parse_uuid
from app.api.serializers import run_out
from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.schemas.agent import StartScanRequest
from app.scrapers import SCRAPERS
from app.services import agent_orchestrator as orch
from app.services.agent_orchestrator import get_master_resume
from app.services.notifier import push_update
from app.services.rate_limiter import rate_limiter
from app.worker.dispatch import enqueue

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/start-scan", status_code=202)
def start_scan(body: StartScanRequest, user: CurrentUser, db: DB) -> dict:
    platforms = orch.scan_platforms(user.prefs, body.platforms)  # your sources, plus the top companies
    unknown = [p for p in platforms if p not in SCRAPERS]
    if unknown:
        raise HTTPException(422, f"Unknown platforms: {unknown}")
    orch.close_stale_scans(db, user.id)
    running = db.scalar(select(AgentRun).where(AgentRun.user_id == user.id, AgentRun.run_type == "scan",
                                               AgentRun.status == "running",
                                               AgentRun.started_at > datetime.now(UTC) - timedelta(hours=1)))
    if running:
        raise HTTPException(status.HTTP_409_CONFLICT, "A scan is already running")
    run = AgentRun(user_id=user.id, run_type="scan", trigger="user", status="running",
                   log=[{"ts": datetime.now(UTC).isoformat(), "level": "info", "message": "Scan queued"}])
    db.add(run)
    db.flush()
    enqueue("scan_user", str(user.id), "user", platforms, str(run.id), after_commit=db)
    return run_out(run, include_log=True)


@router.get("/status")
def agent_status(user: CurrentUser, db: DB) -> dict:
    counts = dict(db.execute(select(Application.status, func.count()).where(Application.user_id == user.id)
                             .group_by(Application.status)).all())
    orch.close_stale_scans(db, user.id)  # a scan the app was stopped in the middle of
    running = db.scalars(select(AgentRun).where(AgentRun.user_id == user.id, AgentRun.status == "running")
                         .order_by(AgentRun.started_at.desc()).limit(5)).all()
    prefs = user.prefs
    interval = int(prefs.get("scan_interval_hours") or 6)
    next_scan = (user.last_scan_at + timedelta(hours=interval)).isoformat() if user.last_scan_at and prefs.get("scan_enabled", True) else None
    return {
        "has_master_resume": get_master_resume(db, user) is not None,
        "to_review": orch.waiting_count(db, user),
        "review_mode": prefs.get("review_mode") or "swipe",
        "pending_approval": counts.get(ApplicationStatus.PENDING_APPROVAL, 0),
        "preparing": counts.get(ApplicationStatus.PREPARING, 0),
        "approved": counts.get(ApplicationStatus.APPROVED, 0),
        "failed": counts.get(ApplicationStatus.FAILED, 0),
        "applied_today": rate_limiter.applications_today(str(user.id)),
        "daily_limit": prefs.get("max_applications_per_day"),
        "running_runs": [run_out(r) for r in running],
        "last_scan_at": user.last_scan_at.isoformat() if user.last_scan_at else None,
        "next_scan_at": next_scan,
        "scan_enabled": prefs.get("scan_enabled", True),
        "google_connected": user.google_connected,
        "linkedin_connected": bool(user.linkedin_session_cookie),
    }


@router.get("/runs")
def list_runs(user: CurrentUser, db: DB, run_type: str | None = None,
              page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100)) -> dict:
    query = select(AgentRun).where(AgentRun.user_id == user.id)
    if run_type:
        query = query.where(AgentRun.run_type == run_type)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    runs = db.scalars(query.order_by(AgentRun.started_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [run_out(r) for r in runs], "total": total, "page": page}


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: CurrentUser, db: DB) -> dict:
    run = db.get(AgentRun, parse_uuid(run_id))
    if run is None or run.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run_out(run, include_log=True)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, user: CurrentUser, db: DB) -> dict:
    run = db.get(AgentRun, parse_uuid(run_id))
    if run is None or run.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if run.status == "running":
        run.status = "cancelled"  # a running scan notices within a second or two and stops
        run.completed_at = datetime.now(UTC)
        push_update(str(user.id), "agent_run_updated", {"id": str(run.id), "status": "cancelled"})
    return run_out(run)


@router.post("/check-email", status_code=202)
def check_email(user: CurrentUser, db: DB) -> dict:
    if not user.google_connected:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Connect your Google account first")
    enqueue("check_user_email", str(user.id), after_commit=db)
    return {"queued": True}
