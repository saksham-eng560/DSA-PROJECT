from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONType, UTCDateTime, utcnow
from app.core.security import EncryptedJSON, EncryptedText

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.resume import Resume

DEFAULT_PREFERENCES: dict[str, Any] = {
    # --- PLAN.md §4 defaults ---
    "target_roles": [],
    "target_locations": ["Delhi, India", "India"],
    "remote_preference": "any",  # remote | hybrid | onsite | any
    "salary_min": None,
    "salary_max": None,
    "salary_currency": "USD",
    "experience_level": ["internship"],
    "industries": [],
    "company_size_preference": [],
    "companies_to_avoid": [],
    "companies_to_target": [],
    "max_applications_per_day": 25,
    "auto_apply_threshold": 80,
    "job_types": ["internship"],  # internships only for now; add "full-time" to widen
    # --- Intern level (services/intern_level.py) ---
    "internships_only": True,  # intern roles only, at every company: no full-time, new-grad or contract jobs
    "year_of_study": 2,        # 1-5 (None = any): skips "final-year only", "rising seniors", PhD / MBA-only internships
    "graduation_year": None,   # None = read from your resume's education (else estimated from your year)
    # --- Tech focus (services/role_focus.py); empty = no filter. The "AI engineer · Python" preset fills both ---
    "focus_skills": [],  # e.g. AI, LLMs, Python, FastAPI: a posting must name at least one
    "avoid_skills": [],  # e.g. Java, Spring Boot: roles in these are skipped (unless they also ask for your stack)
    # --- Internship length (services/duration_fit.py) ---
    "max_internship_months": None,  # e.g. 2: postings stating a longer internship are skipped; None = any length
    "notification_channels": ["email", "dashboard"],
    "notification_popups": True,  # off: no pop-ups in the dashboard or the OS; notifications still collect in the bell
    # --- Extended settings ---
    "keywords_exclude": [],
    "posted_within_days": 14,
    "scan_enabled": True,
    "scan_interval_hours": 6,
    "platforms": ["internshala", "linkedin", "naukri", "unstop", "remote_boards", "internships", "greenhouse", "lever",
                  "ashby", "workday", "generic"],
    # Saved since the preset gained Naukri, Unstop and remote boards: a 4-source list here is your own choice
    # (older saved settings with the preset's earlier list are upgraded once, ``presets.upgrade_earlier_platforms``).
    "platforms_upgraded": True,
    # --- Where and when (services/location_focus.py) ---
    # ~country_share % of each scan's new postings stay in the country; prime cities are shown first.
    "location_focus": {
        "enabled": True,
        "country": "India",
        "prime_cities": ["Delhi", "New Delhi", "Delhi NCR", "Gurugram", "Gurgaon", "Noida", "Greater Noida",
                         "Faridabad", "Ghaziabad"],
        "country_share": 90,
    },
    "internship_season": "Summer 2027",  # postings for another term are skipped; empty = any season
    # --- Progress tracking ---
    "progress_updates_everywhere": True,  # every application update goes to Gmail + dashboard + chat
    "progress_digest": "daily",           # daily | weekly | off: e-mail summary of every application
    # --- Swipe Review / mass apply ---
    # "swipe": nothing is skipped for a low score; every job that passes your hard filters waits in
    #          Swipe Review, and the jobs you keep are prepared and applied to.
    # "auto":  the original behaviour: jobs under auto_apply_threshold are skipped, the rest prepared.
    "review_mode": "swipe",
    "resume_strategy": "original",  # original (your file, untouched) | light (reorder only) | full (AI rewrite)
    "auto_submit_kept": True,       # kept jobs are submitted once filled, unless a question needs you
    "trust_generated_answers": True,  # AI-written open-ended answers don't hold a kept job back
    "auto_keep_min_score": None,    # optionally keep jobs scoring at least this without swiping
    "max_jobs_per_source": None,    # None = server default (MAX_JOBS_PER_SOURCE)
    "fill_deck_to": 40,  # fewer cards waiting: near misses of your tech focus / length / verified-only fill up (0 = off)
    "source_share": 40,  # no job site fills more than this % of a scan's new postings while others have some
    "exclude_no_sponsorship": False,  # skip listings that say they don't sponsor visas
    # --- Internshala apply bot (opt-in; Internshala's terms don't allow automated access) ---
    "internshala_bot_enabled": False,  # fill Internshala applications with your synced Internshala login
    "internshala_auto_submit": False,  # send them without your click (otherwise they wait for you)
    "internshala_daily_limit": 15,     # at most this many Internshala applications a day (1-25)
    "internshala_share": 25,           # at most this % of each scan's new postings come from Internshala (0-100)
    "internshala_per_scan": 10,        # and never more than this many new Internshala postings per scan (0-50)
    # --- Company check (services/company_verifier.py) ---
    "skip_suspicious_companies": True,  # postings with scam signs (fees, WhatsApp-only, MLM...) are skipped
    "verified_companies_only": False,   # True: only renowned companies, startups on their own job board, ones you trust
    "trusted_companies": [],            # companies you marked legit: applied to automatically like verified ones
    "scan_top_companies": True,         # every scan also searches big tech, product companies, top startups, AI
    "sources": {
        # ATS boards to crawl directly (public APIs, no login needed)
        "greenhouse_boards": [],   # e.g. ["stripe", "airbnb"]
        "lever_companies": [],     # e.g. ["netflix"]
        "ashby_boards": [],        # e.g. ["openai"]
        "workday_sites": [],       # e.g. ["https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"]
        "career_pages": [],        # any careers page URL (JSON-LD / ATS link detection)
        # Curated internship lists (GitHub-hosted JSON, updated daily); names or raw JSON URLs
        "internship_lists": ["simplify-internships", "vanshb03-internships"],
    },
    "discord_webhook_url": None,
    "slack_webhook_url": None,
    "timezone": "Asia/Kolkata",
    "cover_letter_enabled": True,
}


def default_preferences() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_PREFERENCES)


def merge_preferences(current: dict[str, Any] | None, updates: dict[str, Any] | None) -> dict[str, Any]:
    merged = default_preferences()
    for source in (current or {}, updates or {}):
        for key, value in source.items():
            if key == "sources" and isinstance(value, dict):
                merged["sources"] = {**merged.get("sources", {}), **value}
            else:
                merged[key] = value
    return merged


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50))
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str | None] = mapped_column(String(255))

    # OAuth tokens (AES-256-GCM encrypted at rest)
    google_access_token: Mapped[str | None] = mapped_column(EncryptedText)
    google_refresh_token: Mapped[str | None] = mapped_column(EncryptedText)
    google_token_expiry: Mapped[datetime | None] = mapped_column(UTCDateTime)
    google_scopes: Mapped[list[str] | None] = mapped_column(JSONType)
    google_email: Mapped[str | None] = mapped_column(String(255))
    gmail_history_id: Mapped[str | None] = mapped_column(String(64))
    gmail_watch_expiration: Mapped[datetime | None] = mapped_column(UTCDateTime)
    gmail_last_polled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    linkedin_session_cookie: Mapped[str | None] = mapped_column(EncryptedText)
    linkedin_cookie_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    linkedin_session_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    linkedin_profile_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    # Internshala login (cookies synced by the browser extension), encrypted JSON list of cookies
    internshala_session: Mapped[list[dict[str, Any]] | None] = mapped_column(EncryptedJSON)
    internshala_session_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    internshala_session_valid: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # The browser those cookies belong to: the bot presents the same one (sites may tie a login to it)
    internshala_user_agent: Mapped[str | None] = mapped_column(String(512))

    # Per-ATS login credentials (e.g. Workday tenant accounts), encrypted JSON
    ats_credentials: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON)

    preferences: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=default_preferences)

    consents: Mapped[dict[str, Any] | None] = mapped_column(JSONType, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    resumes: Mapped[list[Resume]] = relationship(back_populates="user", cascade="all, delete-orphan")
    applications: Mapped[list[Application]] = relationship(back_populates="user", cascade="all, delete-orphan")
    field_mappings: Mapped[list[UserFieldMapping]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def first_name(self) -> str:
        return (self.full_name or "").split(" ")[0]

    @property
    def last_name(self) -> str:
        parts = (self.full_name or "").split(" ")
        return " ".join(parts[1:]) if len(parts) > 1 else ""

    @property
    def prefs(self) -> dict[str, Any]:
        return merge_preferences(self.preferences, None)

    @property
    def google_connected(self) -> bool:
        return bool(self.google_refresh_token or self.google_access_token)


class UserFieldMapping(Base):
    __tablename__ = "user_field_mappings"
    __table_args__ = (UniqueConstraint("user_id", "field_name", name="uq_user_field_mapping"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    field_value: Mapped[str] = mapped_column(Text, nullable=False)
    field_type: Mapped[str | None] = mapped_column(String(50))

    user: Mapped[User] = relationship(back_populates="field_mappings")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("idx_notifications_user_unread", "user_id", "is_read", "created_at"),)  # the bell's unread count

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
