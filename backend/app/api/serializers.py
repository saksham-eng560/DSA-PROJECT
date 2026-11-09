"""Model -> JSON serializers for API responses."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from app.config import settings
from app.models.agent_run import AgentRun
from app.models.application import Application, ApplicationStatusHistory
from app.models.communication import Communication
from app.models.enums import ApplicationStatus
from app.models.interview import Interview
from app.models.job import Job
from app.models.resume import Resume
from app.models.user import Notification, User
from app.services.company_verifier import summary as company_summary
from app.services.intern_level import year_fit


def iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


def enum(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def file_url(key: str | None) -> str | None:
    if not key:
        return None
    return f"{settings.API_PREFIX}/files/{quote(key)}"


def user_out(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "phone": user.phone,
        "location": user.location,
        "linkedin_url": user.linkedin_url,
        "has_password": bool(user.hashed_password),
        "google_connected": user.google_connected,
        "google_email": user.google_email,
        "linkedin_connected": bool(user.linkedin_session_cookie),
        "linkedin_session_valid": user.linkedin_session_valid,
        "preferences": user.prefs,
        "last_scan_at": iso(user.last_scan_at),
        "created_at": iso(user.created_at),
    }


MANUAL_URL_PREFIX = "https://manual.autoapply.invalid/"  # applications you logged without a link


def job_out(job: Job, application: Application | None = None, prefs: dict[str, Any] | None = None) -> dict[str, Any]:
    """``prefs``: your own "Mark legit" choices shown in the company verdict."""
    out = {
        "id": str(job.id),
        "company_name": job.company_name,
        "company_logo_url": job.company_logo_url,
        "role_title": job.role_title,
        "location": job.location,
        "is_remote": job.is_remote,
        "job_type": enum(job.job_type),
        "experience_level": enum(job.experience_level),
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "source_url": None if job.source_url.startswith(MANUAL_URL_PREFIX) else job.source_url,
        "source_platform": enum(job.source_platform),
        "application_url": job.application_url,
        "easy_apply": job.easy_apply,
        "extracted_skills": job.extracted_skills or [],
        "posted_date": iso(job.posted_date),
        "deadline_date": iso(job.deadline_date),
        "discovered_at": iso(job.discovered_at),
        "is_active": job.is_active,
        "company": company_summary(job, prefs),
        "year_fit": year_fit(job, prefs),  # "Open to 2nd-year students" when the posting names your year
    }
    if application is not None:
        out["application"] = {
            "id": str(application.id),
            "status": enum(application.status),
            "match_score": application.match_score,
            "match_reasoning": application.match_reasoning,
            "similarity_score": application.similarity_score,
        }
    return out


def job_detail_out(job: Job, application: Application | None = None, prefs: dict[str, Any] | None = None) -> dict[str, Any]:
    if prefs is None and application is not None and application.user is not None:
        prefs = application.user.prefs
    return {**job_out(job, application, prefs), "description": job.description, "requirements": job.requirements,
            "nice_to_haves": job.nice_to_haves}


def is_self_applied(app: Application) -> bool:
    """You told us you applied on your own ("I Applied") rather than the agent submitting it."""
    return any(h.new_status == ApplicationStatus.APPLIED and h.changed_by == "user" for h in app.history)


def application_summary(app: Application, self_applied: bool | None = None) -> dict[str, Any]:
    job = app.job
    return {
        "self_applied": is_self_applied(app) if self_applied is None else self_applied,
        "id": str(app.id),
        "status": enum(app.status),
        "match_score": app.match_score,
        "match_reasoning": app.match_reasoning,
        "ats_platform": enum(app.ats_platform),
        "needs_manual_review": app.needs_manual_review,
        "manual_review_reason": app.manual_review_reason,
        "created_at": iso(app.created_at),
        "updated_at": iso(app.updated_at),
        "submitted_at": iso(app.submitted_at),
        "job": job_out(job, prefs=app.user.prefs if app.user is not None else None) if job else None,
    }


def history_out(h: ApplicationStatusHistory) -> dict[str, Any]:
    return {"id": str(h.id), "old_status": enum(h.old_status), "new_status": enum(h.new_status),
            "changed_by": h.changed_by, "notes": h.notes, "created_at": iso(h.created_at)}


def resume_out(resume: Resume, include_content: bool = True) -> dict[str, Any]:
    out = {
        "id": str(resume.id),
        "label": resume.label,
        "original_filename": resume.original_filename,
        "is_master": resume.is_master,
        "version": resume.version,
        "parent_resume_id": str(resume.parent_resume_id) if resume.parent_resume_id else None,
        "tailored_for_job_id": str(resume.tailored_for_job_id) if resume.tailored_for_job_id else None,
        "changes_made": resume.changes_made or [],
        "pdf_url": file_url(resume.pdf_url),
        "original_file_url": file_url(resume.original_file_url),
        "created_at": iso(resume.created_at),
        "updated_at": iso(resume.updated_at),
    }
    if include_content:
        out["parsed_content"] = resume.parsed_content
    return out


def application_detail(app: Application, communications: list[Communication], interviews: list[Interview]) -> dict[str, Any]:
    out = application_summary(app)
    out.update(
        {
            "job": job_detail_out(app.job, prefs=app.user.prefs if app.user is not None else None) if app.job else None,
            "match_details": app.match_details,
            "similarity_score": app.similarity_score,
            "cover_letter": app.cover_letter,
            "custom_answers": app.custom_answers or [],
            "field_overrides": app.field_overrides or {},
            "form_fields": app.form_fields or [],
            "tailored_resume": resume_out(app.tailored_resume) if app.tailored_resume else None,
            "tailored_resume_pdf_url": file_url(app.tailored_resume_pdf_url),
            "form_screenshot_url": file_url(app.form_screenshot_url),
            "confirmation_screenshot_url": file_url(app.confirmation_screenshot_url),
            "confirmation_number": app.confirmation_number,
            "staged_at": iso(app.staged_at),
            "approved_at": iso(app.approved_at),
            "rejection_reason": app.rejection_reason,
            "offer_details": app.offer_details,
            "error_log": app.error_log,
            "retry_count": app.retry_count,
            "notes": app.notes,
            "history": [history_out(h) for h in app.history],
            "communications": [communication_out(c, brief=True) for c in communications],
            "interviews": [interview_out(i, brief=True) for i in interviews],
        }
    )
    return out


def communication_out(c: Communication, brief: bool = False) -> dict[str, Any]:
    out = {
        "id": str(c.id),
        "application_id": str(c.application_id) if c.application_id else None,
        "direction": enum(c.direction),
        "sender_email": c.sender_email,
        "sender_name": c.sender_name,
        "subject": c.subject,
        "detected_intent": enum(c.detected_intent),
        "intent_confidence": c.intent_confidence,
        "urgency": c.urgency,
        "is_action_required": c.is_action_required,
        "action_taken": c.action_taken,
        "received_at": iso(c.received_at),
        "snippet": (c.body_text or "")[:220],
    }
    if not brief:
        out.update({
            "body_text": c.body_text,
            "extracted_details": c.extracted_details,
            "suggested_reply": c.suggested_reply,
            "gmail_thread_id": c.gmail_thread_id,
            "gmail_draft_id": c.gmail_draft_id,
            "attachments": c.attachments or [],
        })
    return out


def interview_out(i: Interview, brief: bool = False) -> dict[str, Any]:
    app = i.application
    out = {
        "id": str(i.id),
        "application_id": str(i.application_id),
        "company_name": app.job.company_name if app and app.job else None,
        "role_title": app.job.role_title if app and app.job else None,
        "interview_type": enum(i.interview_type),
        "scheduled_at": iso(i.scheduled_at),
        "duration_minutes": i.duration_minutes,
        "timezone": i.timezone,
        "meeting_link": i.meeting_link,
        "meeting_platform": i.meeting_platform,
        "physical_location": i.physical_location,
        "interviewer_names": i.interviewer_names or [],
        "outcome": i.outcome,
        "google_event_id": i.google_event_id,
        "google_event_link": i.google_event_link,
    }
    if not brief:
        out.update({"prep_notes": i.prep_notes, "company_research": i.company_research,
                    "likely_questions": i.likely_questions or [], "feedback": i.feedback})
    return out


def run_out(run: AgentRun, include_log: bool = False) -> dict[str, Any]:
    out = {
        "id": str(run.id),
        "run_type": run.run_type,
        "status": run.status,
        "trigger": run.trigger,
        "jobs_discovered": run.jobs_discovered,
        "jobs_matched": run.jobs_matched,
        "applications_prepared": run.applications_prepared,
        "applications_submitted": run.applications_submitted,
        "errors_count": run.errors_count,
        "started_at": iso(run.started_at),
        "completed_at": iso(run.completed_at),
        "duration_seconds": run.duration_seconds,
        "progress": run.progress,
    }
    if include_log:
        out["log"] = run.log or []
    return out


def notification_out(n: Notification) -> dict[str, Any]:
    return {"id": str(n.id), "event_type": n.event_type, "title": n.title, "body": n.body, "link": n.link,
            "data": n.data or {}, "is_read": n.is_read, "created_at": iso(n.created_at)}
