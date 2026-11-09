"""Enumerations mirroring the PostgreSQL ENUM types defined in PLAN.md §4."""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class JobType(str, enum.Enum):
    FULL_TIME = "full-time"
    PART_TIME = "part-time"
    INTERNSHIP = "internship"
    CONTRACT = "contract"
    FREELANCE = "freelance"


class ExperienceLevel(str, enum.Enum):
    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    EXECUTIVE = "executive"
    INTERNSHIP = "internship"


class ATSPlatform(str, enum.Enum):
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    GLASSDOOR = "glassdoor"
    WELLFOUND = "wellfound"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    ASHBY = "ashby"
    BAMBOOHR = "bamboohr"
    ICIMS = "icims"
    TALEO = "taleo"
    SMARTRECRUITERS = "smartrecruiters"
    JOBVITE = "jobvite"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class ApplicationStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    MATCHED = "matched"
    SKIPPED = "skipped"
    PREPARING = "preparing"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    APPLIED = "applied"
    ACKNOWLEDGED = "acknowledged"
    SCREENING = "screening"
    INTERVIEW = "interview"
    ASSESSMENT = "assessment"
    FINAL_ROUND = "final_round"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    FAILED = "failed"


# Statuses that mean "the application reached the employer".
SUBMITTED_STATUSES = {
    ApplicationStatus.APPLIED,
    ApplicationStatus.ACKNOWLEDGED,
    ApplicationStatus.SCREENING,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.ASSESSMENT,
    ApplicationStatus.FINAL_ROUND,
    ApplicationStatus.OFFER,
    ApplicationStatus.ACCEPTED,
    ApplicationStatus.REJECTED,
}

# Statuses that count as the employer responding.
RESPONSE_STATUSES = {
    ApplicationStatus.ACKNOWLEDGED,
    ApplicationStatus.SCREENING,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.ASSESSMENT,
    ApplicationStatus.FINAL_ROUND,
    ApplicationStatus.OFFER,
    ApplicationStatus.ACCEPTED,
    ApplicationStatus.REJECTED,
}

INTERVIEW_STATUSES = {
    ApplicationStatus.SCREENING,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.ASSESSMENT,
    ApplicationStatus.FINAL_ROUND,
    ApplicationStatus.OFFER,
    ApplicationStatus.ACCEPTED,
}

# Pipeline ordering used so that e-mail parsing never moves an application backwards.
STATUS_RANK = {
    ApplicationStatus.DISCOVERED: 0,
    ApplicationStatus.MATCHED: 1,
    ApplicationStatus.SKIPPED: 1,
    ApplicationStatus.PREPARING: 2,
    ApplicationStatus.PENDING_APPROVAL: 3,
    ApplicationStatus.APPROVED: 4,
    ApplicationStatus.FAILED: 4,
    ApplicationStatus.APPLIED: 5,
    ApplicationStatus.ACKNOWLEDGED: 6,
    ApplicationStatus.SCREENING: 7,
    ApplicationStatus.ASSESSMENT: 8,
    ApplicationStatus.INTERVIEW: 9,
    ApplicationStatus.FINAL_ROUND: 10,
    ApplicationStatus.OFFER: 11,
    ApplicationStatus.ACCEPTED: 12,
    ApplicationStatus.REJECTED: 12,
    ApplicationStatus.WITHDRAWN: 12,
}


class EmailDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class EmailIntent(str, enum.Enum):
    ACKNOWLEDGMENT = "acknowledgment"
    REJECTION = "rejection"
    INTERVIEW_INVITE = "interview_invite"
    ASSESSMENT = "assessment"
    OFFER = "offer"
    FOLLOW_UP = "follow_up"
    INFO_REQUEST = "info_request"
    GENERIC = "generic"
    UNKNOWN = "unknown"


class InterviewType(str, enum.Enum):
    PHONE_SCREEN = "phone_screen"
    VIDEO_CALL = "video_call"
    ONSITE = "onsite"
    TECHNICAL = "technical"
    BEHAVIORAL = "behavioral"
    PANEL = "panel"
    TAKE_HOME = "take_home"
    PAIR_PROGRAMMING = "pair_programming"
    OTHER = "other"


def pg_enum(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    """SQLAlchemy Enum persisted by *value* (e.g. 'full-time') with a named PG type."""
    return SAEnum(
        enum_cls,
        name=name,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


# Shared type instances so each PostgreSQL ENUM is declared exactly once.
JOB_TYPE_ENUM = pg_enum(JobType, "job_type")
EXPERIENCE_LEVEL_ENUM = pg_enum(ExperienceLevel, "experience_level")
ATS_PLATFORM_ENUM = pg_enum(ATSPlatform, "ats_platform")
APPLICATION_STATUS_ENUM = pg_enum(ApplicationStatus, "application_status")
EMAIL_DIRECTION_ENUM = pg_enum(EmailDirection, "email_direction")
EMAIL_INTENT_ENUM = pg_enum(EmailIntent, "email_intent")
INTERVIEW_TYPE_ENUM = pg_enum(InterviewType, "interview_type")
