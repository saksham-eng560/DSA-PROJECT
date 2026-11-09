"""Recruiter e-mail understanding (PLAN.md §8 TASK: EMAIL INTENT PARSING)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

from app.models.enums import ApplicationStatus, EmailIntent
from app.services import llm_schemas
from app.services.llm import LLMError, get_llm, render_prompt
from app.services.text_utils import normalize_company, normalize_text, truncate

logger = logging.getLogger(__name__)

ATS_SENDER_DOMAINS = (
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "hire.lever.co", "myworkday.com", "workday.com",
    "ashbyhq.com", "smartrecruiters.com", "icims.com", "jobvite.com", "bamboohr.com", "taleo.net",
    "successfactors.com", "linkedin.com", "indeed.com", "indeedemail.com", "glassdoor.com", "wellfound.com",
    "hackerrank.com", "codesignal.com", "codility.com", "hirevue.com", "calendly.com", "goodtime.io",
    "gem.com", "rippling.com", "workable.com", "recruitee.com", "breezy.hr", "jazzhr.com", "paylocity.com",
)
GENERIC_MAIL_DOMAINS = ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "proton.me", "protonmail.com")

INTENT_TO_STATUS: dict[str, ApplicationStatus | None] = {
    "acknowledgment": ApplicationStatus.ACKNOWLEDGED,
    "rejection": ApplicationStatus.REJECTED,
    "interview_invite": ApplicationStatus.INTERVIEW,
    "assessment": ApplicationStatus.ASSESSMENT,
    "offer": ApplicationStatus.OFFER,
    "follow_up": None,
    "info_request": None,
    "generic": None,
}

_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("offer", ("pleased to offer", "offer letter", "extend an offer", "extend you an offer", "offer of employment",
               "formal offer", "excited to offer you")),
    ("rejection", ("unfortunately", "not moving forward", "not be moving forward", "decided to move forward with other",
                   "pursue other candidates", "decided not to proceed", "regret to inform", "position has been filled",
                   "will not be proceeding", "not selected", "other applicants whose", "not to move forward",
                   "no longer considering", "we have decided to go with")),
    ("assessment", ("coding challenge", "take-home", "take home assignment", "online assessment", "hackerrank",
                    "codesignal", "codility", "technical assessment", "coding assessment", "skills assessment",
                    "complete the assessment", "karat")),
    ("interview_invite", ("schedule an interview", "invite you to interview", "interview invitation", "phone screen",
                          "schedule a call", "schedule a time", "your availability", "next round", "onsite interview",
                          "video interview", "calendly.com", "interview with", "would like to speak with you",
                          "set up a call", "technical interview", "virtual interview", "interview is scheduled",
                          "interview confirmation")),
    ("info_request", ("please send", "could you provide", "please provide", "additional information",
                      "please complete the following", "background check", "references")),
    ("acknowledgment", ("received your application", "thank you for applying", "thanks for applying",
                        "application has been received", "application was received", "we have received your application",
                        "thank you for your interest", "thanks for your interest", "application submitted",
                        "successfully submitted", "we've received your application")),
    ("follow_up", ("following up", "checking in", "touch base", "circle back", "quick question")),
]

_TZ_ABBREVIATIONS = {
    "PT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "MT": "America/Denver", "MST": "America/Denver", "MDT": "America/Denver",
    "CT": "America/Chicago", "CST": "America/Chicago", "CDT": "America/Chicago",
    "ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York",
    "GMT": "UTC", "UTC": "UTC", "BST": "Europe/London", "CET": "Europe/Paris", "CEST": "Europe/Paris",
    "IST": "Asia/Kolkata", "SGT": "Asia/Singapore", "JST": "Asia/Tokyo", "AEST": "Australia/Sydney",
}


def _tzinfos(name: str, offset: int | None) -> Any:
    zone = _TZ_ABBREVIATIONS.get((name or "").upper())
    return ZoneInfo(zone) if zone else None


MEETING_LINK = re.compile(
    r"https?://(?:[\w-]+\.)?(?:zoom\.us/[jw]/\S+|meet\.google\.com/[\w-]+|teams\.microsoft\.com/\S+|"
    r"teams\.live\.com/\S+|[\w-]+\.webex\.com/\S+|calendly\.com/\S+|app\.coderpad\.io/\S+|meet\.jit\.si/\S+)",
    re.IGNORECASE,
)


@dataclass
class EmailMessage:
    sender_email: str
    subject: str
    body: str
    sender_name: str = ""
    received_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def sender_domain(self) -> str:
        return self.sender_email.split("@")[-1].lower().strip(">") if "@" in self.sender_email else ""


def meeting_platform(link: str | None) -> str | None:
    if not link:
        return None
    link = link.lower()
    for key, name in (("zoom.us", "zoom"), ("meet.google", "google_meet"), ("teams.", "teams"), ("webex", "webex"),
                      ("calendly", "calendly"), ("coderpad", "coderpad"), ("jit.si", "jitsi")):
        if key in link:
            return name
    return "other"


def is_job_related(msg: EmailMessage, company_names: list[str]) -> bool:
    domain = msg.sender_domain
    if any(domain == d or domain.endswith("." + d) for d in ATS_SENDER_DOMAINS):
        return True
    text = normalize_text(f"{msg.subject} {msg.body[:3000]}")
    for name in company_names:
        n = normalize_company(name)
        if n and len(n) > 2 and (n.replace(" ", "") in domain.replace("-", "") or re.search(rf"\b{re.escape(n)}\b", text)):
            if re.search(r"application|interview|position|role|candida|recruit|hiring|offer|opportunit", text):
                return True
    return bool(re.search(r"\b(your application|thank you for applying|interview|recruiter|talent acquisition|hiring team)\b", text))


def match_application(msg: EmailMessage, applications: list[dict[str, Any]]) -> str | None:
    """Return the id of the application this e-mail most likely refers to."""
    domain = msg.sender_domain.replace("-", "")
    text = normalize_text(f"{msg.sender_name} {msg.subject} {msg.body[:4000]}")
    best: tuple[int, str] | None = None
    for app in applications:
        company = normalize_company(app.get("company_name"))
        if not company:
            continue
        score = 0
        compact = company.replace(" ", "")
        if compact and compact in domain and domain not in GENERIC_MAIL_DOMAINS:
            score += 5
        if re.search(rf"\b{re.escape(company)}\b", text):
            score += 3
        title = normalize_text(app.get("role_title") or "")
        if title and title in text:
            score += 2
        if score and (best is None or score > best[0]):
            best = (score, str(app["id"]))
    return best[1] if best else None


def _extract_datetime(text: str, reference: datetime, tz: str) -> str:
    """Best-effort extraction of an interview date/time from free text."""
    try:
        zone = ZoneInfo(tz or "UTC")
    except Exception:  # noqa: BLE001
        zone = ZoneInfo("UTC")
    candidates = re.findall(
        r"((?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+)?"
        r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?"
        r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})"
        r"(?:,?\s+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?(?:\s*[a-z]{2,4}\b)?))?",
        text,
        re.IGNORECASE,
    )
    ref_local = reference.astimezone(zone)
    for _weekday, day, time_part in candidates:
        raw = f"{day} {time_part}".strip()
        try:
            default = ref_local.replace(hour=9, minute=0, second=0, microsecond=0, tzinfo=None)
            parsed = dateparser.parse(
                re.sub(r"(\d)(st|nd|rd|th)", r"\1", raw), fuzzy=True, default=default, tzinfos=_tzinfos
            )
        except (ValueError, OverflowError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        if parsed < ref_local - timedelta(days=1):
            try:
                parsed = parsed.replace(year=parsed.year + 1)
            except ValueError:
                continue
        return parsed.isoformat()
    return ""


def heuristic_parse(msg: EmailMessage, applications: list[dict[str, Any]], candidate_name: str, tz: str = "UTC") -> dict[str, Any]:
    text = normalize_text(f"{msg.subject}\n{msg.body}")
    intent, confidence = "generic", 0.4
    for name, phrases in _RULES:
        hits = sum(1 for p in phrases if p in text)
        if hits:
            intent, confidence = name, min(0.9, 0.55 + 0.15 * hits)
            break
    # "Unfortunately" in an interview-rescheduling mail is not a rejection
    if intent == "rejection" and re.search(r"reschedul|another time|new time", text):
        intent, confidence = "interview_invite", 0.5

    link_match = MEETING_LINK.search(msg.body or "")
    link = link_match.group(0).rstrip(").,>\"'") if link_match else ""
    reference = msg.received_at or datetime.now(UTC)
    interview_date = _extract_datetime(msg.body or "", reference, tz) if intent in ("interview_invite", "assessment") else ""
    interview_type = ""
    if intent == "interview_invite":
        if re.search(r"phone screen|phone call|quick call|recruiter call|intro call", text):
            interview_type = "phone_screen"
        elif re.search(r"onsite|on-site|in person|in-person", text):
            interview_type = "onsite"
        elif re.search(r"technical|coding|system design|pair program", text):
            interview_type = "technical"
        elif re.search(r"behavio", text):
            interview_type = "behavioral"
        elif link:
            interview_type = "video_call"
    deadline = ""
    m = re.search(r"(?:within|in the next)\s+(\d+)\s+(day|hour|week)s?", text)
    if m and intent in ("assessment", "info_request", "offer"):
        delta = {"day": timedelta(days=int(m.group(1))), "hour": timedelta(hours=int(m.group(1))),
                 "week": timedelta(weeks=int(m.group(1)))}[m.group(2)]
        deadline = (reference + delta).isoformat()

    status = INTENT_TO_STATUS.get(intent)
    if intent == "interview_invite" and interview_type == "phone_screen":
        status = ApplicationStatus.SCREENING
    if intent == "interview_invite" and re.search(r"final round|final interview|onsite", text):
        status = ApplicationStatus.FINAL_ROUND

    sender_first = (msg.sender_name or "").split(" ")[0] or "there"
    reply = ""
    if intent == "interview_invite":
        reply = (f"Hi {sender_first},\n\nThank you so much for reaching out — I'd love to interview. "
                 f"{'The proposed time works for me. ' if interview_date else 'Here is my availability over the next week: [add times]. '}"
                 f"Please let me know if you need anything else from me beforehand.\n\nBest regards,\n{candidate_name}")
    elif intent == "assessment":
        reply = (f"Hi {sender_first},\n\nThank you for the next step! I've received the assessment details and will "
                 f"complete it within the requested timeframe.\n\nBest regards,\n{candidate_name}")
    elif intent == "offer":
        reply = (f"Hi {sender_first},\n\nThank you so much — I'm thrilled to receive this offer. I'm reviewing the details "
                 f"and will get back to you shortly. Could you share a good time to discuss a few questions?\n\n"
                 f"Best regards,\n{candidate_name}")
    elif intent in ("info_request", "follow_up"):
        reply = f"Hi {sender_first},\n\nThanks for your note! [Answer here]\n\nBest regards,\n{candidate_name}"

    return {
        "intent": intent,
        "confidence": round(confidence, 2),
        "company_name": "",
        "matched_application_id": match_application(msg, applications) or "",
        "extracted_details": {
            "interview_date": interview_date,
            "interview_type": interview_type,
            "duration_minutes": 30 if interview_type == "phone_screen" else (60 if interview_type else 0),
            "meeting_link": link,
            "interviewer_name": msg.sender_name if intent == "interview_invite" else "",
            "deadline": deadline,
            "next_steps": "",
        },
        "suggested_reply": reply,
        "urgency": "high" if intent in ("interview_invite", "offer", "assessment") else ("medium" if intent == "info_request" else "low"),
        "status_update": status.value if status else "none",
        "method": "heuristic",
    }


def analyze_email(
    msg: EmailMessage, applications: list[dict[str, Any]], candidate_name: str, timezone: str = "UTC"
) -> dict[str, Any]:
    llm = get_llm()
    if llm.available:
        try:
            data = llm.complete_json(
                render_prompt(
                    "email_parser",
                    today=datetime.now(UTC).strftime("%A %Y-%m-%d"),
                    timezone=timezone,
                    candidate_name=candidate_name,
                    applications_json=[
                        {"id": str(a["id"]), "company_name": a.get("company_name"), "role_title": a.get("role_title"),
                         "status": a.get("status")} for a in applications[:60]
                    ],
                    sender=f"{msg.sender_name} <{msg.sender_email}>",
                    subject=msg.subject,
                    received_at=(msg.received_at or datetime.now(UTC)).isoformat(),
                    body=truncate(msg.body, 12000),
                ),
                schema=llm_schemas.EMAIL_INTENT_SCHEMA,
                effort="low",
                task="email_parse",
            )
            intent = data.get("intent") if data.get("intent") in INTENT_TO_STATUS else "generic"
            details = data.get("extracted_details") or {}
            app_ids = {str(a["id"]) for a in applications}
            matched = data.get("matched_application_id") if data.get("matched_application_id") in app_ids else None
            status_update = data.get("status_update") or "none"
            valid_status = {s.value for s in ApplicationStatus}
            return {
                "intent": intent,
                "confidence": float(data.get("confidence") or 0.7),
                "company_name": data.get("company_name") or "",
                "matched_application_id": matched or match_application(msg, applications) or "",
                "extracted_details": {
                    "interview_date": details.get("interview_date") or "",
                    "interview_type": details.get("interview_type") or "",
                    "duration_minutes": int(details.get("duration_minutes") or 0),
                    "meeting_link": details.get("meeting_link") or "",
                    "interviewer_name": details.get("interviewer_name") or "",
                    "deadline": details.get("deadline") or "",
                    "next_steps": details.get("next_steps") or "",
                },
                "suggested_reply": data.get("suggested_reply") or "",
                "urgency": data.get("urgency") if data.get("urgency") in ("high", "medium", "low") else "medium",
                "status_update": status_update if status_update in valid_status else "none",
                "method": "llm",
            }
        except LLMError as exc:
            logger.warning("LLM email parsing failed: %s", exc)
    return heuristic_parse(msg, applications, candidate_name, timezone)


def email_intent_enum(intent: str) -> EmailIntent:
    try:
        return EmailIntent(intent)
    except ValueError:
        return EmailIntent.UNKNOWN
