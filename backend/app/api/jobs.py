"""Discovered jobs (user-scoped view joined with the user's application for each job)."""

from __future__ import annotations

from collections import Counter

import anyio
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select

from app.api.deps import DB, CurrentUser, parse_uuid
from app.api.serializers import application_summary, job_detail_out, job_out
from app.models.application import Application
from app.models.enums import ApplicationStatus, ATSPlatform
from app.models.job import Job
from app.schemas.job import JobImportRequest
from app.scrapers import ScraperError
from app.services import agent_orchestrator as orch
from app.services import intern_level
from app.services.application_service import set_status
from app.services.company_catalog import CATALOG, TIERS, normalize_company
from app.services.job_matcher import filter_reasons
from app.worker.dispatch import enqueue

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    user: CurrentUser,
    db: DB,
    q: str | None = None,
    platform: str | None = None,
    remote: bool | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    min_score: int | None = None,
    sort: str = "match",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict:
    query = (
        select(Job, Application)
        .join(Application, and_(Application.job_id == Job.id, Application.user_id == user.id))
        .where(Job.is_active.is_(True))
    )
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(func.lower(Job.role_title).like(like), func.lower(Job.company_name).like(like),
                                func.lower(Job.location).like(like)))
    if platform:
        try:
            query = query.where(Job.source_platform == ATSPlatform(platform))
        except ValueError as exc:
            raise HTTPException(422, "Unknown platform") from exc
    if remote is not None:
        query = query.where(Job.is_remote.is_(remote))
    if status_filter:
        try:
            query = query.where(Application.status == ApplicationStatus(status_filter))
        except ValueError as exc:
            raise HTTPException(422, "Unknown status") from exc
    if min_score is not None:
        query = query.where(Application.match_score >= min_score)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    order = {
        "match": (Application.match_score.desc().nulls_last(), Job.discovered_at.desc()),
        "recent": (Job.discovered_at.desc(),),
        "posted": (Job.posted_date.desc().nulls_last(),),
        "company": (Job.company_name.asc(),),
    }.get(sort, (Job.discovered_at.desc(),))
    rows = db.execute(query.order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [job_out(job, app, user.prefs) for job, app in rows], "total": total, "page": page,
            "page_size": page_size}


@router.get("/top-companies")
def top_companies(user: CurrentUser, db: DB, tier: str | None = None, q: str | None = None,
                  limit: int = Query(default=200, ge=1, le=500)) -> dict:
    """Internships at big tech, product companies, renowned Indian / global startups and AI companies."""
    if tier and tier not in TIERS:
        raise HTTPException(422, "Unknown tier")
    orch.backfill_company_checks(db)
    orch.skip_ineligible_waiting(db, user)
    prefs = user.prefs
    master = orch.get_master_resume(db, user)
    checked = intern_level.with_resume(prefs, master.parsed_content if master else None)
    rows = db.execute(select(Job, Application)
                      .join(Application, and_(Application.job_id == Job.id, Application.user_id == user.id))
                      .where(Job.is_active.is_(True), Job.company_tier.is_not(None))
                      .order_by(Application.match_score.desc().nulls_last(), Job.discovered_at.desc())
                      .limit(5000)).all()
    # What your filters keep: internships for you, in your tech focus, no company you avoid
    rows = [(job, app) for job, app in rows if not filter_reasons(job, checked)[0]]
    counts = Counter(job.company_tier for job, _ in rows)
    if tier:
        rows = [(job, app) for job, app in rows if job.company_tier == tier]
    if q:
        needle = q.lower()
        rows = [(job, app) for job, app in rows
                if any(needle in (value or "").lower() for value in (job.role_title, job.company_name, job.location))]
    return {
        "tiers": [{"key": key, "label": label, "count": counts.get(key, 0)} for key, label in TIERS.items()],
        "total": sum(counts.values()),
        "items": [job_out(job, app, prefs) for job, app in rows[:limit]],
        "catalog": {key: [c.name for c in CATALOG if c.tier == key] for key in TIERS},
        "scan_top_companies": bool(prefs.get("scan_top_companies", True)),
    }


class CompanyTrustIn(BaseModel):
    company: str = Field(min_length=1, max_length=255)
    trusted: bool | None  # true: legit · false: not legit (avoid) · null: back to the agent's verdict


@router.post("/company-trust")
def company_trust(body: CompanyTrustIn, user: CurrentUser, db: DB) -> dict:
    """Your word on a company beats the company check. Legit: the agent applies to it automatically (and sends
    what was waiting only for that). Not legit: it's avoided and its waiting jobs are skipped."""
    name = body.company.strip()
    key = normalize_company(name)
    if not key:
        raise HTTPException(422, "Company name required")
    prefs = user.prefs
    trusted = [t for t in prefs.get("trusted_companies") or [] if normalize_company(t) != key]
    avoid = [a for a in prefs.get("companies_to_avoid") or [] if normalize_company(a) != key]
    if body.trusted is True:
        trusted.append(name)
    elif body.trusted is False:
        avoid.append(name)
    user.preferences = {**(user.preferences or {}), "trusted_companies": trusted, "companies_to_avoid": avoid}
    mine = db.scalars(select(Application).join(Job, Job.id == Application.job_id)
                      .where(Application.user_id == user.id,
                             Application.status.in_((ApplicationStatus.DISCOVERED, ApplicationStatus.MATCHED,
                                                     ApplicationStatus.PENDING_APPROVAL)))).all()
    mine = [a for a in mine if normalize_company(a.job.company_name) == key]
    changed = 0
    for app in mine:
        if body.trusted is False:
            app.match_reasoning = f"You marked {name} as not legit"
            set_status(db, app, ApplicationStatus.SKIPPED, "user", app.match_reasoning)
            changed += 1
        elif body.trusted is True and app.status == ApplicationStatus.PENDING_APPROVAL \
                and (app.manual_review_reason or "").startswith("Not sent automatically:"):
            app.needs_manual_review = False
            app.manual_review_reason = None
            orch.ready_or_submit(db, user, app)  # held only because the company wasn't verified
            changed += 1
    return {"company": name, "trusted": body.trusted, "applications_updated": changed}


@router.get("/{job_id}")
def get_job(job_id: str, user: CurrentUser, db: DB) -> dict:
    job = db.get(Job, parse_uuid(job_id))
    app = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == parse_uuid(job_id)))
    if job is None or app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job_detail_out(job, app)


@router.post("/import", status_code=201)
async def import_job(body: JobImportRequest, user: CurrentUser, db: DB) -> dict:
    """Add a job by URL (any Greenhouse/Lever/Ashby/Workday/LinkedIn/careers page)."""
    try:
        app = await anyio.to_thread.run_sync(orch.import_job_url, db, user, body.url)
    except ScraperError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Without a master resume there is nothing to tailor yet; the job stays in the list to prepare later.
    if (body.prepare and orch.get_master_resume(db, user) is not None
            and app.status in (ApplicationStatus.MATCHED, ApplicationStatus.SKIPPED, ApplicationStatus.DISCOVERED)):
        set_status(db, app, ApplicationStatus.PREPARING, "user", "Prepared on request")
        enqueue("prepare_application", str(app.id), after_commit=db)
    return application_summary(app)


@router.post("/{job_id}/evaluate")
def evaluate_job(job_id: str, user: CurrentUser, db: DB) -> dict:
    app = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == parse_uuid(job_id)))
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    master = orch.get_master_resume(db, user)
    if master is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a master resume first")
    if app.status in (ApplicationStatus.SKIPPED, ApplicationStatus.MATCHED):
        app.status = ApplicationStatus.DISCOVERED
    orch.evaluate_application(db, user, app, master, use_llm=True, resume_vec=orch.resume_embedding(db, master))
    return job_detail_out(app.job, app)


@router.post("/{job_id}/prepare", status_code=202)
def prepare_job(job_id: str, user: CurrentUser, db: DB) -> dict:
    """Manually pick a job: tailor resume, write cover letter, fill the form, then wait for approval."""
    app = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == parse_uuid(job_id)))
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if orch.get_master_resume(db, user) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a master resume first")
    if app.status not in (ApplicationStatus.DISCOVERED, ApplicationStatus.MATCHED, ApplicationStatus.SKIPPED,
                          ApplicationStatus.FAILED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Application is already {app.status.value}")
    app.auto_submit = False  # it waits for your approval, even if you once kept it with "Apply automatically"
    set_status(db, app, ApplicationStatus.PREPARING, "user", "Prepared on request")
    enqueue("prepare_application", str(app.id), after_commit=db)
    return application_summary(app)
