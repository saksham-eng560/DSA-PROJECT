"""JSON schemas passed to Claude structured outputs (``output_config.format``).

Every object lists all of its properties as required and sets ``additionalProperties: false``;
optional values are represented as empty strings / arrays instead of missing keys.
"""

from __future__ import annotations

from typing import Any

STR: dict[str, Any] = {"type": "string"}
INT: dict[str, Any] = {"type": "integer"}
NUM: dict[str, Any] = {"type": "number"}
BOOL: dict[str, Any] = {"type": "boolean"}


def obj(**props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def enum(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


STR_LIST = arr(STR)

RESUME_SCHEMA = obj(
    personal_info=obj(name=STR, email=STR, phone=STR, location=STR, linkedin=STR, github=STR, portfolio=STR),
    summary=STR,
    education=arr(obj(institution=STR, degree=STR, field=STR, gpa=STR, start_date=STR, end_date=STR, highlights=STR_LIST)),
    experience=arr(obj(company=STR, title=STR, start_date=STR, end_date=STR, location=STR, bullets=STR_LIST)),
    projects=arr(obj(name=STR, description=STR, technologies=STR_LIST, url=STR)),
    skills=obj(technical=STR_LIST, languages=STR_LIST, tools=STR_LIST, soft_skills=STR_LIST),
    certifications=arr(obj(name=STR, issuer=STR, date=STR)),
    awards=STR_LIST,
)

JOB_EVALUATION_SCHEMA = obj(
    evaluation=obj(
        match_score=INT,
        skills_match=INT,
        experience_match=INT,
        industry_match=INT,
        location_match=INT,
        compensation_match=INT,
        proceed_with_application=BOOL,
        reasoning=STR,
        missing_skills=STR_LIST,
        strong_matches=STR_LIST,
    )
)

TAILORED_RESUME_SCHEMA = obj(tailored_resume=RESUME_SCHEMA, changes_made=STR_LIST)

COVER_LETTER_SCHEMA = obj(cover_letter=STR, tone=STR, word_count=INT)

CUSTOM_ANSWERS_SCHEMA = obj(
    custom_answers=arr(
        obj(
            question=STR,
            field_type=enum("text", "select", "radio", "checkbox", "number"),
            answer=STR,
            confidence=NUM,
            needs_user_review=BOOL,
        )
    )
)

EMAIL_INTENT_SCHEMA = obj(
    intent=enum(
        "acknowledgment",
        "rejection",
        "interview_invite",
        "assessment",
        "offer",
        "follow_up",
        "info_request",
        "generic",
    ),
    confidence=NUM,
    company_name=STR,
    matched_application_id=STR,
    extracted_details=obj(
        interview_date=STR,
        interview_type=STR,
        duration_minutes=INT,
        meeting_link=STR,
        interviewer_name=STR,
        deadline=STR,
        next_steps=STR,
    ),
    suggested_reply=STR,
    urgency=enum("high", "medium", "low"),
    status_update=STR,
)

INTERVIEW_PREP_SCHEMA = obj(
    prep_notes=STR,
    company_research=STR,
    likely_questions=arr(obj(question=STR, answer_outline=STR)),
)

FORM_MAPPING_SCHEMA = obj(
    mappings=arr(
        obj(
            field_id=STR,
            value_source=enum("profile", "resume_file", "cover_letter", "answer", "skip"),
            profile_key=STR,
            value=STR,
            confidence=NUM,
            needs_user_review=BOOL,
        )
    )
)

LINKEDIN_DIFF_SCHEMA = obj(
    has_changes=BOOL,
    changes=arr(obj(section=STR, change=STR, linkedin_value=STR)),
    summary=STR,
)

CONNECTION_TEST_SCHEMA = obj(ok=BOOL, reply=STR)
