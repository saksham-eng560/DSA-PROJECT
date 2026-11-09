"""Profile, preferences, field mappings, integrations and privacy endpoints."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, ExtensionUser, limiter
from app.api.serializers import user_out
from app.automation.browser import BrowserUnavailable
from app.automation.proxy import proxy_manager
from app.config import settings
from app.models.user import UserFieldMapping, merge_preferences
from app.schemas.user import (
    ATSCredentialsUpdate,
    DeleteAccountRequest,
    FieldMappingsUpdate,
    InternshalaSessionIn,
    LinkedInCookieIn,
    PreferencesUpdate,
    ProfileUpdate,
)
from app.services import agent_orchestrator as orch
from app.services import intern_level
from app.services.ai_setup import PullError, connection_test, llm_section, pull_progress, start_pull
from app.services.google_oauth import has_scope
from app.services.location_focus import get_season
from app.services.presets import apply_preset
from app.services.privacy import delete_user_data, export_user_data
from app.services.progress import send_now as send_progress_now
from app.services.question_answerer import STANDARD_FIELDS
from app.submitters import internshala_apply
from app.worker.dispatch import enqueue

router = APIRouter(prefix="/users/me", tags=["users"])

ALLOWED_PREF_KEYS = set(merge_preferences(None, None)) | {"resume_template", "auto_draft_replies"}


@router.get("")
def get_me(user: CurrentUser) -> dict:
    return user_out(user)


@router.patch("")
def update_me(body: ProfileUpdate, user: CurrentUser) -> dict:
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    return user_out(user)


@router.get("/preferences")
def get_preferences(user: CurrentUser) -> dict:
    return user.prefs


@router.get("/student")
def get_student(user: CurrentUser, db: DB) -> dict:
    """Your year of study and graduation year as the intern-level filter sees them (and where the year came from)."""
    master = orch.get_master_resume(db, user)
    info = intern_level.student(user.prefs, master.parsed_content if master else None)
    return {"internships_only": intern_level.internships_only(user.prefs), "year_of_study": info.year,
            "graduation_year": info.graduation_year, "graduation_year_source": info.source}


def _validate_focus(prefs: dict) -> None:  # type: ignore[type-arg]
    focus = prefs.get("location_focus")
    if focus is not None:
        if not isinstance(focus, dict):
            raise HTTPException(422, "location_focus must be an object")
        share = focus.get("country_share", 90)
        if not isinstance(share, int) or not 0 <= share <= 100:
            raise HTTPException(422, "location_focus.country_share must be 0-100")
        cities = focus.get("prime_cities") or []
        if not isinstance(cities, list) or not all(isinstance(c, str) for c in cities) or len(cities) > 50:
            raise HTTPException(422, "location_focus.prime_cities must be a list of city names")
        if not isinstance(focus.get("country") or "", str):
            raise HTTPException(422, "location_focus.country must be text")
    season = prefs.get("internship_season")
    if season and (not isinstance(season, str) or get_season({"internship_season": season}) is None):
        raise HTTPException(422, "internship_season must look like 'Summer 2027' (or be empty)")
    if prefs.get("progress_digest") not in ("daily", "weekly", "off"):
        raise HTTPException(422, "progress_digest must be 'daily', 'weekly' or 'off'")


def _validate_internshala(prefs: dict) -> None:  # type: ignore[type-arg]
    for key in ("internshala_bot_enabled", "internshala_auto_submit"):
        if not isinstance(prefs.get(key), bool):
            raise HTTPException(422, f"{key} must be true or false")
    limit = prefs.get("internshala_daily_limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 25:
        raise HTTPException(422, "internshala_daily_limit must be 1-25")
    share = prefs.get("internshala_share")
    if not isinstance(share, int) or isinstance(share, bool) or not 0 <= share <= 100:
        raise HTTPException(422, "internshala_share must be 0-100")
    per_scan = prefs.get("internshala_per_scan")
    if not isinstance(per_scan, int) or isinstance(per_scan, bool) or not 0 <= per_scan <= 50:
        raise HTTPException(422, "internshala_per_scan must be 0-50")
    year = prefs.get("year_of_study")
    if year is not None and (not isinstance(year, int) or isinstance(year, bool) or not 1 <= year <= 5):
        raise HTTPException(422, "year_of_study must be 1-5 (or empty for any year)")
    months = prefs.get("max_internship_months")
    if months is not None and (not isinstance(months, (int, float)) or isinstance(months, bool) or not 1 <= months <= 12):
        raise HTTPException(422, "max_internship_months must be 1-12 (or empty for any length)")
    grad = prefs.get("graduation_year")
    if grad is not None and (not isinstance(grad, int) or isinstance(grad, bool) or not 2000 <= grad <= 2100):
        raise HTTPException(422, "graduation_year must be a year like 2029 (or empty)")
    for key in ("focus_skills", "avoid_skills"):
        skills = prefs.get(key)
        if not isinstance(skills, list) or len(skills) > 50 or not all(isinstance(s, str) and 0 < len(s.strip()) <= 60
                                                                       for s in skills):
            raise HTTPException(422, f"{key} must be a list of skills (up to 50, each up to 60 characters)")
    for key in ("skip_suspicious_companies", "scan_top_companies", "internships_only", "verified_companies_only"):
        if not isinstance(prefs.get(key), bool):
            raise HTTPException(422, f"{key} must be true or false")
    trusted = prefs.get("trusted_companies")
    if not isinstance(trusted, list) or len(trusted) > 500 or not all(isinstance(t, str) and len(t) <= 255 for t in trusted):
        raise HTTPException(422, "trusted_companies must be a list of company names")


@router.put("/preferences")
def update_preferences(body: PreferencesUpdate, user: CurrentUser, db: DB) -> dict:
    unknown = set(body.preferences) - ALLOWED_PREF_KEYS
    if unknown:
        raise HTTPException(422, f"Unknown preference keys: {sorted(unknown)}")
    prefs = merge_preferences(user.preferences, body.preferences)
    threshold = prefs.get("auto_apply_threshold")
    if not isinstance(threshold, int) or not 0 <= threshold <= 100:
        raise HTTPException(422, "auto_apply_threshold must be 0-100")
    max_daily = prefs.get("max_applications_per_day")
    if not isinstance(max_daily, int) or not 1 <= max_daily <= 200:
        raise HTTPException(422, "max_applications_per_day must be 1-200")
    if prefs.get("review_mode") not in ("swipe", "auto"):
        raise HTTPException(422, "review_mode must be 'swipe' or 'auto'")
    if prefs.get("resume_strategy") not in ("original", "light", "full"):
        raise HTTPException(422, "resume_strategy must be 'original', 'light' or 'full'")
    auto_keep = prefs.get("auto_keep_min_score")
    if auto_keep is not None and (not isinstance(auto_keep, int) or not 0 <= auto_keep <= 100):
        raise HTTPException(422, "auto_keep_min_score must be empty or 0-100")
    per_source = prefs.get("max_jobs_per_source")
    if per_source is not None and (not isinstance(per_source, int) or not 10 <= per_source <= 1000):
        raise HTTPException(422, "max_jobs_per_source must be empty or 10-1000")
    for key, high in (("fill_deck_to", 200), ("source_share", 100)):
        value = prefs.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= high:
            raise HTTPException(422, f"{key} must be 0-{high}")
    if not isinstance(prefs.get("notification_popups"), bool):
        raise HTTPException(422, "notification_popups must be true or false")
    _validate_focus(prefs)
    _validate_internshala(prefs)
    turned_on = prefs["internshala_bot_enabled"] and not user.prefs.get("internshala_bot_enabled")
    if turned_on:
        consents = dict(user.consents or {})
        consents["internshala_bot"] = datetime.now(UTC).isoformat()  # you turned it on after the terms warning
        user.consents = consents
    user.preferences = prefs
    if turned_on:
        orch.restage_internshala_waiting(db, user)  # fill the real forms of anything prepared while it was off
    return prefs


@router.post("/progress-report")
def progress_report(user: CurrentUser, db: DB) -> dict:
    """Send the progress digest now (Gmail + dashboard + Discord/Slack), covering the last 7 days."""
    return {"sent": send_progress_now(db, user, days=7)}


@router.post("/preferences/preset/{name}")
def apply_preference_preset(name: str, user: CurrentUser) -> dict:
    """One click to mass apply: internships, startups or new-grad roles (your own lists are kept)."""
    try:
        user.preferences = apply_preset(user.preferences, name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return user.prefs


# ------------------------------------------------------------------ field mappings
def _mapping_out(m: UserFieldMapping) -> dict:
    secret = "password" in m.field_name
    return {"field_name": m.field_name, "field_value": "********" if secret else m.field_value,
            "field_type": m.field_type, "is_secret": secret}


@router.get("/field-mappings")
def list_field_mappings(user: CurrentUser) -> dict:
    return {"mappings": [_mapping_out(m) for m in user.field_mappings], "standard_fields": STANDARD_FIELDS}


@router.put("/field-mappings")
def upsert_field_mappings(body: FieldMappingsUpdate, user: CurrentUser, db: DB) -> dict:
    existing = {m.field_name: m for m in user.field_mappings}
    for item in body.mappings:
        name = item.field_name.strip().lower().replace(" ", "_")
        if "password" in name:
            # Secrets live in the encrypted ats_credentials column, never in plaintext mappings.
            if item.field_value and item.field_value != "********":
                creds = dict(user.ats_credentials or {})
                creds[name] = item.field_value
                user.ats_credentials = creds
            continue
        if name in existing:
            existing[name].field_value = item.field_value
            existing[name].field_type = item.field_type
        else:
            db.add(UserFieldMapping(user_id=user.id, field_name=name, field_value=item.field_value, field_type=item.field_type))
    db.flush()
    db.refresh(user)
    mappings = [_mapping_out(m) for m in user.field_mappings]
    for key in (user.ats_credentials or {}):
        mappings.append({"field_name": key, "field_value": "********", "field_type": "password", "is_secret": True})
    return {"mappings": mappings, "standard_fields": STANDARD_FIELDS}


@router.delete("/field-mappings/{field_name}")
def delete_field_mapping(field_name: str, user: CurrentUser, db: DB) -> dict:
    mapping = db.scalar(select(UserFieldMapping).where(UserFieldMapping.user_id == user.id,
                                                       UserFieldMapping.field_name == field_name))
    if mapping:
        db.delete(mapping)
    if user.ats_credentials and field_name in user.ats_credentials:
        creds = dict(user.ats_credentials)
        creds.pop(field_name)
        user.ats_credentials = creds
    return {"ok": True}


@router.put("/ats-credentials")
def update_ats_credentials(body: ATSCredentialsUpdate, user: CurrentUser) -> dict:
    creds = dict(user.ats_credentials or {})
    creds.update({k: v for k, v in body.credentials.items() if v})
    user.ats_credentials = creds
    return {"keys": sorted(creds)}


# ------------------------------------------------------------------ integrations
@router.get("/integrations")
def integrations(user: CurrentUser) -> dict:
    return {
        "google": {
            "configured": settings.google_configured,
            "connected": user.google_connected,
            "email": user.google_email,
            "gmail": has_scope(user, "gmail"),
            "calendar": has_scope(user, "calendar"),
            "last_polled_at": user.gmail_last_polled_at.isoformat() if user.gmail_last_polled_at else None,
            "push_enabled": bool(settings.GMAIL_PUBSUB_TOPIC),
        },
        "linkedin": {
            "connected": bool(user.linkedin_session_cookie),
            "session_valid": user.linkedin_session_valid,
            "updated_at": user.linkedin_cookie_updated_at.isoformat() if user.linkedin_cookie_updated_at else None,
            "profile_diff": (user.linkedin_profile_snapshot or {}).get("diff"),
            "synced_at": (user.linkedin_profile_snapshot or {}).get("synced_at"),
        },
        "internshala": {  # never the cookies themselves
            "connected": bool(user.internshala_session),
            "session_valid": bool(user.internshala_session_valid),
            "updated_at": user.internshala_session_updated_at.isoformat() if user.internshala_session_updated_at else None,
            "bot_enabled": bool(user.prefs.get("internshala_bot_enabled")),
            "auto_submit": bool(user.prefs.get("internshala_auto_submit")),
            "daily_limit": int(user.prefs.get("internshala_daily_limit") or 15),
        },
        "llm": llm_section(),
        "automation": {"proxies": len(proxy_manager.urls), "captcha": bool(settings.CAPTCHA_API_KEY),
                       "dry_run": settings.SUBMISSION_DRY_RUN, "auto_stage": settings.AUTO_STAGE_APPLICATIONS},
        "notifications": {"smtp": bool(settings.SMTP_HOST), "discord": bool(user.prefs.get("discord_webhook_url") or settings.DISCORD_WEBHOOK_URL),
                          "slack": bool(user.prefs.get("slack_webhook_url") or settings.SLACK_WEBHOOK_URL)},
        "ats_credentials": sorted((user.ats_credentials or {}).keys()),
    }


# AI model: keys live only in .env on the machine running the app; these endpoints never take or show one.
@router.post("/integrations/llm/test")
@limiter.limit("10/minute")
def test_llm(request: Request, user: CurrentUser) -> dict:
    """Send one tiny request to the configured AI model: {ok, provider, model, latency_ms, sample | error, hint}."""
    return connection_test()


@router.post("/integrations/ollama/pull", status_code=202)
@limiter.limit("10/minute")
def pull_ollama_model(request: Request, user: CurrentUser) -> dict:
    """Start downloading OLLAMA_MODEL into Ollama in the background."""
    try:
        return start_pull()
    except PullError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/integrations/ollama/pull")
def ollama_pull_progress(user: CurrentUser) -> dict:
    """Download progress: {status: idle | pulling | success | error, completed, total, percent, error}."""
    return pull_progress()


@router.post("/integrations/linkedin-cookie")
def sync_linkedin_cookie(body: LinkedInCookieIn, user: ExtensionUser) -> dict:
    user.linkedin_session_cookie = body.li_at.strip()
    user.linkedin_cookie_updated_at = datetime.now(UTC)
    user.linkedin_session_valid = True
    if body.profile_url and "linkedin.com/in/" in body.profile_url and not user.linkedin_url:
        user.linkedin_url = body.profile_url.split("?")[0]
    consents = dict(user.consents or {})
    consents["linkedin"] = datetime.now(UTC).isoformat()
    user.consents = consents
    return {"ok": True, "synced_at": user.linkedin_cookie_updated_at.isoformat()}


@router.delete("/integrations/linkedin")
def disconnect_linkedin(user: CurrentUser) -> dict:
    user.linkedin_session_cookie = None
    user.linkedin_session_valid = False
    user.linkedin_profile_snapshot = None
    return {"ok": True}


@router.post("/integrations/linkedin/sync", status_code=202)
def trigger_linkedin_sync(user: CurrentUser, db: DB) -> dict:
    if not user.linkedin_session_cookie:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "LinkedIn session not synced yet")
    enqueue("linkedin_sync_user", str(user.id), after_commit=db)
    return {"queued": True}


INTERNSHALA_DOMAIN = re.compile(r"\.?(?:[a-z0-9-]+\.)*internshala\.com")
COOKIE_NAME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+")
USER_AGENT = re.compile(r"Mozilla/5\.0 [\x20-\x7e]{10,500}")  # a browser's navigator.userAgent, header-safe
MAX_INTERNSHALA_SESSION_CHARS = 32_000


@router.post("/integrations/internshala-session")
def sync_internshala_session(body: InternshalaSessionIn, user: ExtensionUser, db: DB) -> dict:
    """The extension sends your internshala.com cookies (httpOnly ones included); stored encrypted."""
    now = datetime.now(UTC)
    if body.reason != "manual" and (user.consents or {}).get("internshala_disconnected"):
        # You disconnected Internshala in the dashboard: background re-syncs don't undo that.
        raise HTTPException(status.HTTP_409_CONFLICT, "Internshala was disconnected in your dashboard — click "
                                                      "“Sync Internshala session” in the extension to connect it again.")
    cookies: dict[tuple[str, str, str], dict] = {}  # type: ignore[type-arg]
    for c in body.cookies:
        domain = c.domain.strip().lower()
        if not INTERNSHALA_DOMAIN.fullmatch(domain):
            raise HTTPException(422, f"Only internshala.com cookies are accepted, not {domain[:60]}")
        if not COOKIE_NAME.fullmatch(c.name) or any(ord(ch) < 32 or ch in ";\x7f" for ch in c.value):
            raise HTTPException(422, f"Invalid cookie {c.name[:40]}")
        if c.expiration_date is not None and c.expiration_date < now.timestamp():
            continue  # already expired
        path = c.path or "/"
        cookies[(c.name, domain, path)] = {
            "name": c.name, "value": c.value, "domain": domain, "path": path, "secure": c.secure, "httpOnly": c.http_only,
            "sameSite": c.same_site, "expirationDate": c.expiration_date,
            "hostOnly": c.host_only if c.host_only is not None else not domain.startswith("."),
        }
    stored = list(cookies.values())
    if sum(len(c["name"]) + len(c["value"]) for c in stored) > MAX_INTERNSHALA_SESSION_CHARS:
        raise HTTPException(413, "Too many Internshala cookies")
    if not any(c["name"] in internshala_apply.SESSION_COOKIES and c["value"] for c in stored):
        raise HTTPException(422, "No Internshala session cookie found — log into Internshala in Chrome and sync again")
    if not internshala_apply.has_login(stored):
        raise HTTPException(422, "You're not logged into Internshala in this browser — log in and sync again")
    user.internshala_session = stored
    if body.user_agent is not None:  # older extensions don't send it: keep the one we have
        user.internshala_user_agent = body.user_agent if USER_AGENT.fullmatch(body.user_agent) else None
    user.internshala_session_updated_at = now
    user.internshala_session_valid = True
    consents = dict(user.consents or {})
    consents["internshala"] = now.isoformat()
    consents.pop("internshala_disconnected", None)
    user.consents = consents
    orch.restage_internshala_waiting(db, user)
    return {"ok": True, "synced_at": now.isoformat(), "cookies": len(stored)}


@router.delete("/integrations/internshala")
def disconnect_internshala(user: CurrentUser) -> dict:
    """Forget the Internshala login and turn the bot off; the extension's automatic re-syncs stop too
    (until you click Sync in the extension yourself)."""
    user.internshala_session = None
    user.internshala_user_agent = None
    user.internshala_session_updated_at = None
    user.internshala_session_valid = False
    user.preferences = {**(user.preferences or {}), "internshala_bot_enabled": False, "internshala_auto_submit": False}
    consents = dict(user.consents or {})
    consents["internshala_disconnected"] = datetime.now(UTC).isoformat()
    user.consents = consents
    return {"ok": True}


@router.post("/integrations/internshala/check")
def check_internshala_session(user: CurrentUser, db: DB) -> dict:
    """Open Internshala with your synced login in a real browser and see whether it's still signed in."""
    if not user.internshala_session:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Internshala login not synced yet — use the browser extension")
    try:
        valid = internshala_apply.check_session(user.internshala_session, user_agent=user.internshala_user_agent)
    except BrowserUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"No browser available to check the session: {exc}") from exc
    except Exception as exc:  # a timeout or network error: report it, keep the stored state
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not reach Internshala: {exc}") from exc
    user.internshala_session_valid = valid
    if valid:
        orch.restage_internshala_waiting(db, user)
    return {"session_valid": valid, "checked_at": datetime.now(UTC).isoformat()}


# ------------------------------------------------------------------ privacy
@router.get("/export")
def export_data(user: CurrentUser, db: DB) -> Response:
    data = export_user_data(db, user)
    return Response(
        content=json.dumps(data, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="autoapply-export-{datetime.now(UTC).date()}.json"'},
    )


@router.delete("")
def delete_account(body: DeleteAccountRequest, user: CurrentUser, db: DB, response: Response) -> dict:
    if body.confirm != "DELETE":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Type DELETE to confirm")
    delete_user_data(db, user)
    response.delete_cookie(settings.COOKIE_NAME, path="/")
    return {"deleted": True}
