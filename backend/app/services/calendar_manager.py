"""Google Calendar integration + AI interview preparation notes."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.interview import Interview
from app.models.user import User
from app.schemas.resume_content import ResumeContent
from app.services import llm_schemas
from app.services.google_oauth import GoogleAuthError, GoogleNotConfigured, build_service, has_scope
from app.services.job_matcher import job_skills, job_text, resume_skill_set
from app.services.llm import LLMError, get_llm, render_prompt
from app.services.text_utils import display_skill, truncate

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- prep notes
def heuristic_prep(resume_content: dict[str, Any], application: Application, interview: Interview) -> dict[str, Any]:
    job = application.job
    resume = ResumeContent.model_validate(resume_content)
    required = job_skills(job)
    have = resume_skill_set(resume)
    matched = [display_skill(s) for s in required if s in have]
    gaps = [display_skill(s) for s in required if s not in have]
    stories = []
    for exp in resume.experience[:3]:
        if exp.bullets:
            stories.append(f"- {exp.title} @ {exp.company}: {exp.bullets[0]}")
    itype = (interview.interview_type.value if interview.interview_type else "interview").replace("_", " ")
    notes = "\n".join(
        [
            f"ROLE: {job.role_title} at {job.company_name} ({itype})",
            f"What they need most: {', '.join(display_skill(s) for s in required[:8]) or 'see job description'}",
            f"Your strongest overlaps: {', '.join(matched[:8]) or 'relevant experience from your resume'}",
            "Stories to tell:",
            *(stories or ["- Pick 2-3 projects that best match the role."]),
            f"Gaps to prepare for: {', '.join(gaps[:6]) or 'none detected'} — be honest and show how you learn quickly.",
            "Questions to ask:",
            "- What does success look like in the first 90 days?",
            "- What are the biggest challenges the team is facing right now?",
            "- How does the team collaborate and make technical decisions?",
        ]
    )
    questions = [
        {"question": "Tell me about yourself.", "answer_outline": resume.summary or "Two-minute career story ending with why this role."},
        {"question": f"Why {job.company_name}?", "answer_outline": "Connect the company's mission from the JD to your motivation."},
        {"question": "Describe a challenging project you led.", "answer_outline": stories[0][2:] if stories else "Use STAR format."},
        {"question": "Tell me about a time you disagreed with a teammate.", "answer_outline": "STAR: situation, how you listened, outcome."},
    ]
    for skill in matched[:4]:
        questions.append({"question": f"How have you used {skill} in production?",
                          "answer_outline": f"Concrete example involving {skill} with measurable results."})
    for skill in gaps[:2]:
        questions.append({"question": f"What is your experience with {skill}?",
                          "answer_outline": "Be honest; relate adjacent experience and how you'd ramp up."})
    return {
        "prep_notes": notes,
        "company_research": f"(Inferred from the job description) {truncate(job.description, 600)}",
        "likely_questions": questions,
        "method": "heuristic",
    }


def generate_prep(resume_content: dict[str, Any], application: Application, interview: Interview) -> dict[str, Any]:
    llm = get_llm()
    if llm.available:
        job = application.job
        try:
            data = llm.complete_json(
                render_prompt(
                    "interview_prep",
                    interview_type=(interview.interview_type.value if interview.interview_type else "general").replace("_", " "),
                    role_title=job.role_title,
                    company_name=job.company_name,
                    job_description_text=truncate(job_text(job), 10000),
                    resume_json=resume_content,
                    interview_details_json={
                        "scheduled_at": interview.scheduled_at.isoformat() if interview.scheduled_at else None,
                        "interviewers": interview.interviewer_names or [],
                        "platform": interview.meeting_platform,
                    },
                ),
                schema=llm_schemas.INTERVIEW_PREP_SCHEMA,
                effort="medium",
                task="interview_prep",
            )
            if data.get("prep_notes"):
                data["method"] = "llm"
                return data
        except LLMError as exc:
            logger.warning("LLM interview prep failed: %s", exc)
    return heuristic_prep(resume_content, application, interview)


# --------------------------------------------------------------------------- calendar
def build_event_body(application: Application, interview: Interview) -> dict[str, Any]:
    job = application.job
    start = interview.scheduled_at
    end = start + timedelta(minutes=interview.duration_minutes or 60)
    description_parts = []
    if interview.meeting_link:
        description_parts.append(f"Join: {interview.meeting_link}")
    if interview.prep_notes:
        description_parts.append("PREP NOTES\n" + interview.prep_notes)
    if interview.likely_questions:
        qs = "\n".join(f"- {q['question'] if isinstance(q, dict) else q}" for q in interview.likely_questions[:12])
        description_parts.append("LIKELY QUESTIONS\n" + qs)
    if interview.company_research:
        description_parts.append("COMPANY RESEARCH\n" + interview.company_research)
    description_parts.append("Created by AutoApply AI")
    return {
        "summary": f"Interview: {job.role_title} @ {job.company_name}",
        "description": "\n\n".join(description_parts)[:7900],
        "location": interview.meeting_link or interview.physical_location or "",
        "start": {"dateTime": start.isoformat(), "timeZone": interview.timezone or "UTC"},
        "end": {"dateTime": end.isoformat(), "timeZone": interview.timezone or "UTC"},
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "email", "minutes": 24 * 60}, {"method": "popup", "minutes": 60}],
        },
        "extendedProperties": {"private": {"autoapply_interview_id": str(interview.id)}},
    }


def calendar_enabled(user: User) -> bool:
    return user.google_connected and has_scope(user, "calendar")


def upsert_calendar_event(db: Session, user: User, application: Application, interview: Interview) -> str | None:
    """Create (or update) the Google Calendar event for an interview. Returns the event id."""
    if not calendar_enabled(user):
        return None
    try:
        service = build_service(db, user, "calendar", "v3")
        body = build_event_body(application, interview)
        if interview.google_event_id:
            event = service.events().update(calendarId="primary", eventId=interview.google_event_id, body=body).execute()
        else:
            event = service.events().insert(calendarId="primary", body=body).execute()
        interview.google_event_id = event.get("id")
        interview.google_event_link = event.get("htmlLink")
        return interview.google_event_id
    except (GoogleAuthError, GoogleNotConfigured) as exc:
        logger.warning("Calendar unavailable for user %s: %s", user.id, exc)
    except Exception as exc:
        logger.exception("Failed to create calendar event: %s", exc)
    return None


def delete_calendar_event(db: Session, user: User, interview: Interview) -> None:
    if not interview.google_event_id or not calendar_enabled(user):
        return
    try:
        service = build_service(db, user, "calendar", "v3")
        service.events().delete(calendarId="primary", eventId=interview.google_event_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to delete calendar event %s: %s", interview.google_event_id, exc)
    interview.google_event_id = None
