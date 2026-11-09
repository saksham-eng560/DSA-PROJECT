"""Application tracking, review & approval (the human-in-the-loop gate)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app.api.deps import DB, CurrentUser, parse_uuid
from app.api.serializers import (
    MANUAL_URL_PREFIX,
    application_detail,
    application_summary,
    file_url,
    history_out,
    iso,
    resume_out,
)
from app.models.application import Application, ApplicationStatusHistory
from app.models.communication import Communication
from app.models.enums import ApplicationStatus, ATSPlatform, JobType
from app.models.interview import Interview
from app.models.job import Job
from app.models.user import User
from app.schemas.application import (
    ApplicationUpdate,
    ApproveRequest,
    DirectSubmitRequest,
    ManualApplicationCreate,
    SelfAppliedRequest,
    StatusUpdate,
    TailoredResumeUpdate,
)
from app.schemas.resume_content import normalize_resume
from app.scrapers import ScrapedJob, detect_ats_platform
from app.services import agent_orchestrator as orch
from app.services import review_sheet
from app.services.application_service import set_status
from app.worker.dispatch import enqueue

router = APIRouter(prefix="/applications", tags=["applications"])

MANUAL_STATUSES = {
    ApplicationStatus.APPLIED, ApplicationStatus.ACKNOWLEDGED, ApplicationStatus.SCREENING, ApplicationStatus.INTERVIEW,
    ApplicationStatus.ASSESSMENT, ApplicationStatus.FINAL_ROUND, ApplicationStatus.OFFER, ApplicationStatus.ACCEPTED,
    ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN, ApplicationStatus.SKIPPED,
}


def _get(db: DB, user_id, application_id: str) -> Application:  # type: ignore[no-untyped-def]
    app = db.get(Application, parse_uuid(application_id))
    if app is None or app.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return app


def _detail(db: DB, app: Application) -> dict:
    comms = db.scalars(select(Communication).where(Communication.application_id == app.id)
                       .order_by(Communication.received_at.desc())).all()
    interviews = db.scalars(select(Interview).where(Interview.application_id == app.id).order_by(Interview.scheduled_at)).all()
    return application_detail(app, list(comms), list(interviews))


@router.get("")
def list_applications(
    user: CurrentUser,
    db: DB,
    status_filter: list[str] | None = Query(default=None, alias="status"),
    q: str | None = None,
    platform: str | None = None,
    needs_review: bool | None = None,
    applied_by: Literal["me", "agent"] | None = None,
    sort: str = "updated",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict:
    query = select(Application).join(Job, Job.id == Application.job_id).where(Application.user_id == user.id)
    if status_filter:
        try:
            statuses = [ApplicationStatus(part) for raw in status_filter for part in raw.split(",") if part]
        except ValueError as exc:
            raise HTTPException(422, "Unknown status") from exc
        query = query.where(Application.status.in_(statuses))
    else:
        # Jobs you haven't picked yet live in Swipe Review / Jobs, not in the applications pipeline.
        query = query.where(Application.status.notin_([ApplicationStatus.DISCOVERED, ApplicationStatus.MATCHED,
                                                       ApplicationStatus.SKIPPED]))
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(func.lower(Job.role_title).like(like), func.lower(Job.company_name).like(like)))
    if platform:
        query = query.where(Job.source_platform == platform)
    if needs_review is not None:
        query = query.where(Application.needs_manual_review.is_(needs_review))
    if applied_by is not None:  # "me" = you clicked "I Applied"; "agent" = the agent submitted it
        mine = _self_applied_exists()
        query = query.where(mine if applied_by == "me" else ~mine)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    order = {
        "updated": Application.updated_at.desc(),
        "match": Application.match_score.desc().nulls_last(),
        "created": Application.created_at.desc(),
        "company": Job.company_name.asc(),
    }.get(sort, Application.updated_at.desc())
    apps = db.scalars(query.order_by(order).offset((page - 1) * page_size).limit(page_size)).all()
    count_query = select(Application.status, func.count()).where(Application.user_id == user.id)
    if applied_by is not None:  # counts for the section you're looking at
        count_query = count_query.where(_self_applied_exists() if applied_by == "me" else ~_self_applied_exists())
    counts = dict(db.execute(count_query.group_by(Application.status)).all())
    mine = set(db.scalars(select(Application.id).where(Application.id.in_([a.id for a in apps]), _self_applied_exists())))
    self_applied_total = db.scalar(select(func.count()).select_from(Application).where(
        Application.user_id == user.id, _self_applied_exists())) or 0
    return {"items": [application_summary(a, self_applied=a.id in mine) for a in apps], "total": total, "page": page,
            "page_size": page_size, "counts": {k.value: v for k, v in counts.items()}, "self_applied_total": self_applied_total}


def _self_applied_exists():  # type: ignore[no-untyped-def]
    return (select(ApplicationStatusHistory.id)
            .where(ApplicationStatusHistory.application_id == Application.id,
                   ApplicationStatusHistory.new_status == ApplicationStatus.APPLIED,
                   ApplicationStatusHistory.changed_by == "user")
            .exists())


# --------------------------------------------------------------------------- "Ready to submit"
def _submit_queue(user_id: uuid.UUID, *columns):  # type: ignore[no-untyped-def]
    """Applications the agent filled and paused for you: best match first, then the longest waiting."""
    return (select(*columns) if columns else select(Application)).where(
        Application.user_id == user_id, Application.status == ApplicationStatus.PENDING_APPROVAL,
    ).order_by(Application.match_score.desc().nulls_last(), Application.staged_at.asc().nulls_last(),
               Application.created_at.asc())


def _queue_item(user: User, app: Application) -> dict:
    job = app.job
    rows = review_sheet.review_rows(app, resume_url=file_url(app.tailored_resume_pdf_url))
    apply_url = job.application_url or (None if job.source_url.startswith(MANUAL_URL_PREFIX) else job.source_url)
    return {
        **application_summary(app),
        "form_screenshot_url": file_url(app.form_screenshot_url),
        "tailored_resume_pdf_url": file_url(app.tailored_resume_pdf_url),
        "staged_at": iso(app.staged_at),
        "blocker": orch.direct_submit_blocker(user, app, probe=False),  # listing never launches Chromium
        # Internshala: what the bot still needs ("bot_off" / "not_synced" / "expired"), or None when it can apply
        "bot": {"site": "Internshala", "missing": orch.internshala_missing(user)} if orch.is_internshala_job(job) else None,
        "apply_url": apply_url,
        "rows": rows,
        "attention": sum(1 for r in rows if review_sheet.needs_attention(r)),
    }


def _next_in_queue(order: list[uuid.UUID], current: uuid.UUID) -> str | None:
    """The queue item after ``current`` (wrapping around to the start); None when nothing else waits."""
    rest = order
    if current in order:
        i = order.index(current)
        rest = order[i + 1:] + order[:i]
    return str(rest[0]) if rest else None


@router.get("/review-queue")
def review_queue(user: CurrentUser, db: DB, limit: int = Query(default=100, ge=1, le=200)) -> dict:
    """"Ready to submit": each paused application as a review sheet, one row per prefilled item."""
    query = _submit_queue(user.id)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    return {"items": [_queue_item(user, a) for a in db.scalars(query.limit(limit)).all()], "total": total}


@router.post("/review-queue/clear")
def clear_review_queue(user: CurrentUser, db: DB) -> dict:
    """Empty "Ready to submit": every paused application is skipped (nothing is sent), history kept."""
    apps = db.scalars(_submit_queue(user.id)).all()
    for app in apps:
        set_status(db, app, ApplicationStatus.SKIPPED, "user", "Cleared from Ready to submit")
    return {"cleared": len(apps)}


@router.post("/{application_id}/submit", status_code=202)
def submit_now(application_id: str, body: DirectSubmitRequest, user: CurrentUser, db: DB) -> dict:
    """Your one click after checking every prefilled row: explicit approval, exactly like /approve.

    Corrections are saved first (profile values and form labels as overrides the submitter types
    verbatim, question answers as your answers), then the normal approval path submits it.
    """
    app = _get(db, user.id, application_id)
    if app.status not in (ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.FAILED):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This application is {app.status.value.replace('_', ' ')}: there's nothing to submit")
    blocker = orch.direct_submit_blocker(user, app)
    if blocker:
        raise HTTPException(status.HTTP_409_CONFLICT, blocker)
    try:
        edits = review_sheet.apply_edits(app, [(r.key, r.value) for r in body.rows], body.cover_letter)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    missing = review_sheet.missing_required(app.form_fields, edits)
    if missing:
        raise HTTPException(422, "Fill in the required field(s) before submitting: " + "; ".join(missing))
    order = list(db.scalars(_submit_queue(user.id, Application.id)))
    app.field_overrides = edits.overrides or None
    note = "Submitted from Ready to submit" + (f" (you corrected: {', '.join(edits.changed)[:300]})" if edits.changed else "")
    try:
        orch.approve_application(db, app, cover_letter=edits.cover_letter, custom_answers=edits.answers, note=note)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {**_detail(db, app), "next_id": _next_in_queue(order, app.id)}


@router.post("/{application_id}/bot-apply", status_code=202)
def bot_apply(application_id: str, user: CurrentUser, db: DB) -> dict:
    """"Apply with the bot" (Internshala): your click is the approval; the bot fills the form and submits it."""
    app = _get(db, user.id, application_id)
    order = list(db.scalars(_submit_queue(user.id, Application.id)))
    try:
        orch.bot_apply(db, user, app)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {**_detail(db, app), "next_id": _next_in_queue(order, app.id)}


@router.get("/{application_id}")
def get_application(application_id: str, user: CurrentUser, db: DB) -> dict:
    return _detail(db, _get(db, user.id, application_id))


@router.patch("/{application_id}")
def update_application(application_id: str, body: ApplicationUpdate, user: CurrentUser, db: DB) -> dict:
    app = _get(db, user.id, application_id)
    if body.cover_letter is not None:
        app.cover_letter = body.cover_letter
    if body.custom_answers is not None:
        app.custom_answers = [a.model_dump(exclude_none=True) for a in body.custom_answers]
    if body.notes is not None:
        app.notes = body.notes
    if body.status:
        try:
            new_status = ApplicationStatus(body.status)
        except ValueError as exc:
            raise HTTPException(422, "Unknown status") from exc
        if new_status not in MANUAL_STATUSES:
            raise HTTPException(422, f"Cannot set status '{new_status.value}' manually")
        set_status(db, app, new_status, "user", "Updated manually")
    return _detail(db, app)


@router.post("/{application_id}/approve", status_code=202)
def approve(application_id: str, body: ApproveRequest, user: CurrentUser, db: DB) -> dict:
    """Explicit user approval — the ONLY path that leads to a submission."""
    app = _get(db, user.id, application_id)
    blocker = orch.direct_submit_blocker(user, app)
    if blocker:  # say it now (e.g. the Internshala bot is off, Chromium is missing), not after a silent failure
        raise HTTPException(status.HTTP_409_CONFLICT, blocker)
    edited =[a.model_dump(exclude_none=True) for a in body.custom_answers] if body.custom_answers is not None else None
    missing = [a.get("question") for a in (edited if edited is not None else app.custom_answers or [])
               if a.get("required") and not str(a.get("answer") or "").strip()]
    if missing:
        raise HTTPException(422, "Answer the required question(s) before approving: " + "; ".join(str(m) for m in missing))
    try:
        orch.approve_application(db, app, cover_letter=body.cover_letter, custom_answers=edited)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _detail(db, app)


@router.post("/{application_id}/skip")
def skip(application_id: str, user: CurrentUser, db: DB) -> dict:
    app = _get(db, user.id, application_id)
    if app.status in (ApplicationStatus.APPLIED, ApplicationStatus.APPROVED):
        raise HTTPException(status.HTTP_409_CONFLICT, "Already submitted / being submitted")
    set_status(db, app, ApplicationStatus.SKIPPED, "user", "Skipped by user")
    return _detail(db, app)


@router.post("/{application_id}/withdraw")
def withdraw(application_id: str, user: CurrentUser, db: DB) -> dict:
    app = _get(db, user.id, application_id)
    set_status(db, app, ApplicationStatus.WITHDRAWN, "user", "Withdrawn by user")
    return _detail(db, app)


@router.post("/{application_id}/mark-applied")
def mark_applied(application_id: str, user: CurrentUser, db: DB, body: SelfAppliedRequest | None = None) -> dict:
    """"I Applied": you applied on your own. The agent stops working on it and tracks it from here."""
    app = _get(db, user.id, application_id)
    body = body or SelfAppliedRequest()
    if body.notes:
        app.notes = f"{app.notes}\n{body.notes}".strip() if app.notes else body.notes
    orch.mark_self_applied(db, user, app, applied_on=body.applied_on)
    return _detail(db, app)


@router.post("/manual", status_code=201)
def log_manual_application(body: ManualApplicationCreate, user: CurrentUser, db: DB) -> dict:
    """Log an application you made anywhere; it lands in "I Applied" and is tracked like the rest."""
    try:
        job_type = JobType(body.job_type)
    except ValueError as exc:
        raise HTTPException(422, "Unknown job type") from exc
    url = (body.url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(422, "The link must start with http:// or https://")
    scraped = ScrapedJob(
        company_name=body.company_name.strip(),
        role_title=body.role_title.strip(),
        description=f"{body.role_title.strip()} at {body.company_name.strip()} (logged by you).",
        source_url=url or f"{MANUAL_URL_PREFIX}{user.id}/{uuid.uuid4()}",
        application_url=url or None,
        source_platform=detect_ats_platform(url) if url else ATSPlatform.CUSTOM,
        location=(body.location or "").strip() or None,
        job_type=job_type,
        raw={"logged_manually": True},
    ).finalize()
    if scraped.source_platform == ATSPlatform.UNKNOWN:
        scraped.source_platform = ATSPlatform.CUSTOM
    job, created = orch.upsert_job(db, scraped)
    if created and not url:
        job.application_url = None
    app, _ = orch.ensure_application(db, user, job)
    if body.notes:
        app.notes = body.notes
    orch.mark_self_applied(db, user, app, applied_on=body.applied_on)
    return _detail(db, app)


@router.post("/{application_id}/restage", status_code=202)
def restage(application_id: str, user: CurrentUser, db: DB) -> dict:
    app = _get(db, user.id, application_id)
    if app.status not in (ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.FAILED):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending or failed applications can be re-staged")
    app.auto_submit = False  # you're reviewing this one yourself now
    set_status(db, app, ApplicationStatus.PREPARING, "user", "Re-filling the form")
    enqueue("stage_application", str(app.id), after_commit=db)
    return _detail(db, app)


@router.post("/{application_id}/prepare", status_code=202)
def reprepare(application_id: str, user: CurrentUser, db: DB) -> dict:
    app = _get(db, user.id, application_id)
    if app.status in (ApplicationStatus.APPROVED, ApplicationStatus.APPLIED):
        raise HTTPException(status.HTTP_409_CONFLICT, "Already submitted / being submitted")
    if orch.get_master_resume(db, user) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a master resume first")
    app.auto_submit = False
    set_status(db, app, ApplicationStatus.PREPARING, "user", "Re-preparing documents")
    enqueue("prepare_application", str(app.id), after_commit=db)
    return _detail(db, app)


@router.put("/{application_id}/resume")
def update_tailored_resume(application_id: str, body: TailoredResumeUpdate, user: CurrentUser, db: DB) -> dict:
    app = _get(db, user.id, application_id)
    if app.tailored_resume is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No tailored resume for this application")
    resume = app.tailored_resume
    resume.parsed_content = normalize_resume(body.parsed_content)
    resume.version = (resume.version or 1) + 1
    app.tailored_resume_pdf_url = orch.render_tailored_pdf(user, resume)
    return resume_out(resume)


@router.post("/{application_id}/status")
def update_status(application_id: str, body: StatusUpdate, user: CurrentUser, db: DB) -> dict:
    app = _get(db, user.id, application_id)
    try:
        new_status = ApplicationStatus(body.status)
    except ValueError as exc:
        raise HTTPException(422, "Unknown status") from exc
    if new_status not in MANUAL_STATUSES:
        raise HTTPException(422, f"Cannot set status '{new_status.value}' manually")
    set_status(db, app, new_status, "user", body.notes or "Updated manually")
    return _detail(db, app)


@router.get("/{application_id}/history")
def history(application_id: str, user: CurrentUser, db: DB) -> dict:
    app = _get(db, user.id, application_id)
    return {"items": [history_out(h) for h in app.history]}
