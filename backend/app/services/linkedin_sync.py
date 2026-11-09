"""LinkedIn profile sync (PLAN.md §5.2): fetch the user's profile with their session cookie and
detect changes versus the stored master resume."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.automation.browser import BrowserSession, BrowserUnavailable, linkedin_cookies
from app.models.resume import Resume
from app.models.user import User
from app.schemas.resume_content import ResumeContent
from app.services import llm_schemas
from app.services.llm import LLMError, get_llm, render_prompt
from app.services.notifier import notify
from app.services.text_utils import extract_skills, normalize_text, truncate

logger = logging.getLogger(__name__)


class LinkedInSessionExpired(Exception):
    pass


def fetch_profile_text(li_at: str, profile_url: str | None) -> str:
    url = (profile_url or "https://www.linkedin.com/in/me/").rstrip("/") + "/"
    with BrowserSession(cookies=linkedin_cookies(li_at)) as session:
        page = session.page
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if "authwall" in page.url or "/login" in page.url or "checkpoint" in page.url:
            raise LinkedInSessionExpired("LinkedIn session expired")
        texts = [page.inner_text("main")]
        for section in ("details/experience", "details/education", "details/skills", "details/certifications"):
            try:
                page.goto(page.url.split("/details")[0].rstrip("/") + f"/{section}/", wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                texts.append(f"== {section} ==\n" + page.inner_text("main"))
            except Exception:  # noqa: BLE001
                continue
        return "\n\n".join(texts)


def heuristic_diff(master: dict[str, Any], profile_text: str) -> dict[str, Any]:
    resume = ResumeContent.model_validate(master)
    master_norm = normalize_text(resume.full_text())
    changes = []
    for skill in extract_skills(profile_text):
        if skill not in master_norm:
            changes.append({"section": "skills", "change": f"New skill on LinkedIn: {skill}", "linkedin_value": skill})
    companies = {normalize_text(e.company) for e in resume.experience}
    for line in profile_text.splitlines():
        m = re.match(r"^(.{2,80}?)\s·\s(Full-time|Part-time|Internship|Contract|Self-employed|Freelance)", line.strip())
        if m and normalize_text(m.group(1)) not in companies:
            changes.append({"section": "experience", "change": f"Position at {m.group(1)} not in master resume",
                            "linkedin_value": line.strip()})
    return {"has_changes": bool(changes), "changes": changes[:20],
            "summary": f"{len(changes)} potential updates found on LinkedIn" if changes else "No changes detected"}


def diff_profile(master: dict[str, Any], profile_text: str) -> dict[str, Any]:
    llm = get_llm()
    if llm.available:
        try:
            return llm.complete_json(
                render_prompt("linkedin_profile_diff", master_resume_json=master, profile_text=truncate(profile_text, 20000)),
                schema=llm_schemas.LINKEDIN_DIFF_SCHEMA,
                effort="low",
                task="linkedin_diff",
            )
        except LLMError as exc:
            logger.warning("LLM LinkedIn diff failed: %s", exc)
    return heuristic_diff(master, profile_text)


def sync_linkedin_profile(db: Session, user: User) -> dict[str, Any]:
    if not user.linkedin_session_cookie:
        return {"status": "not_connected"}
    master = db.scalar(select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True)))
    try:
        text = fetch_profile_text(user.linkedin_session_cookie, user.linkedin_url)
    except LinkedInSessionExpired:
        user.linkedin_session_valid = False
        notify(db, user, "session_expired", "LinkedIn session expired",
               "Open LinkedIn in Chrome and click 'Sync session' in the AutoApply AI extension.", link="/dashboard/settings")
        return {"status": "session_expired"}
    except BrowserUnavailable as exc:
        return {"status": "error", "error": str(exc)}
    user.linkedin_session_valid = True
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    previous = (user.linkedin_profile_snapshot or {}).get("hash")
    snapshot: dict[str, Any] = {"hash": digest, "synced_at": datetime.now(UTC).isoformat(), "text": text[:50000]}
    if master is None:
        user.linkedin_profile_snapshot = snapshot
        return {"status": "no_master_resume"}
    if previous == digest:
        snapshot["diff"] = (user.linkedin_profile_snapshot or {}).get("diff")
        user.linkedin_profile_snapshot = snapshot
        return {"status": "unchanged"}
    diff = diff_profile(master.parsed_content, text)
    snapshot["diff"] = diff
    user.linkedin_profile_snapshot = snapshot
    if diff.get("has_changes"):
        notify(db, user, "linkedin_profile_changed", "Your LinkedIn profile has updates",
               diff.get("summary") or "Review suggested updates to your master resume.", link="/dashboard/resume",
               data={"changes": diff.get("changes", [])[:10]})
    return {"status": "synced", "diff": diff}
