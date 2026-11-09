"""Job <-> candidate matching: preference pre-filter, vector similarity and 5-criterion scoring."""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.models.job import Job
from app.schemas.resume_content import ResumeContent
from app.services import llm_schemas
from app.services.duration_fit import duration_reasons
from app.services.intern_level import intern_level_reasons, internships_only
from app.services.llm import LLMError, get_llm, render_prompt
from app.services.location_focus import (
    TIER_ABROAD,
    TIER_COUNTRY,
    TIER_PRIME,
    get_focus,
    get_season,
    location_tier,
    season_status,
)
from app.services.role_focus import avoid_reasons, off_focus_reasons
from app.services.text_utils import (
    canonical_skill,
    display_skill,
    extract_skills,
    keyword_overlap,
    normalize_company,
    normalize_text,
    truncate,
    years_of_experience_required,
)

logger = logging.getLogger(__name__)

SCORE_KEYS = ("skills_match", "experience_match", "industry_match", "location_match", "compensation_match")

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


# --------------------------------------------------------------------------- helpers
def _parse_resume_date(value: str, default_month: int = 1) -> date | None:
    value = normalize_text(value)
    if not value:
        return None
    if value in ("present", "current", "now", "today"):
        return datetime.now(UTC).date()
    m = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})", value)
    if m:
        return date(int(m.group(2)), _MONTHS[m.group(1)], 1)
    m = re.search(r"(\d{1,2})/(\d{4})", value)
    if m and 1 <= int(m.group(1)) <= 12:
        return date(int(m.group(2)), int(m.group(1)), 1)
    m = re.search(r"(\d{4})", value)
    if m:
        return date(int(m.group(1)), default_month, 1)
    return None


def estimate_years_experience(resume: ResumeContent, include_internships: bool = False) -> float:
    total_days = 0
    for exp in resume.experience:
        if not include_internships and re.search(r"intern", exp.title, re.IGNORECASE):
            continue
        start = _parse_resume_date(exp.start_date)
        end = _parse_resume_date(exp.end_date, default_month=12) or datetime.now(UTC).date()
        if start and end and end >= start:
            total_days += (end - start).days
    return round(total_days / 365.25, 1)


def resume_skill_set(resume: ResumeContent) -> set[str]:
    listed = {canonical_skill(s) for s in resume.skills.all()}
    listed |= {canonical_skill(t) for p in resume.projects for t in p.technologies}
    listed |= set(extract_skills(resume.full_text()))
    return {s for s in listed if s}


def job_text(job: Job) -> str:
    return "\n".join(filter(None, [job.role_title, job.description, job.requirements, job.nice_to_haves]))


def job_skills(job: Job) -> list[str]:
    if job.extracted_skills:
        return [canonical_skill(s) for s in job.extracted_skills]
    return extract_skills(job_text(job))


# --------------------------------------------------------------------------- pre-filter
NO_SPONSORSHIP_PATTERNS = re.compile(
    r"does not offer sponsorship|no (visa )?sponsorship|u\.?s\.? citizenship( is)? required|"
    r"must be a u\.?s\.? citizen|unable to sponsor|not able to sponsor|will not sponsor", re.IGNORECASE)


def filter_reasons(job: Job, prefs: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Split preference checks into (hard, soft) reasons.

    Hard reasons are rules you set explicitly (companies to avoid, excluded keywords, job types,
    expired deadlines, sponsorship): a job that breaks one is never worth your time. Soft reasons
    (title, remote, age of the posting) are only heads-ups in Swipe Review mode.
    """
    fixed, relaxable, soft = split_reasons(job, prefs)
    return fixed + relaxable, soft


def split_reasons(job: Job, prefs: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """(fixed, relaxable, soft) reasons. The hard ones from ``filter_reasons`` come in two kinds:

    * fixed: never shown (a company you avoid, an excluded title, not an internship for you, a passed
      deadline, a possible fraud, abroad when you chose your country only, another season);
    * relaxable: your narrower wishes (tech focus, internship length, verified companies only). When a scan
      would leave Swipe Review thin, the best of these near misses are shown with the reason as a heads-up.
    """
    hard: list[str] = []
    relaxable: list[str] = []
    soft: list[str] = []
    company = normalize_company(job.company_name)
    for avoided in prefs.get("companies_to_avoid") or []:
        if avoided and normalize_company(avoided) and normalize_company(avoided) in company:
            hard.append(f"{job.company_name} is in your companies-to-avoid list")
            break

    text = normalize_text(f"{job.role_title} {job.description[:3000]}")
    for kw in prefs.get("keywords_exclude") or []:
        if kw and re.search(rf"\b{re.escape(normalize_text(kw))}\b", normalize_text(job.role_title)):
            hard.append(f"Title contains excluded keyword '{kw}'")
            break

    job_types = [t for t in (prefs.get("job_types") or []) if t]
    if internships_only(prefs):  # intern roles only, and only ones open to your year of study
        hard.extend(intern_level_reasons(job, prefs))
    elif job_types and job.job_type is not None and job.job_type.value not in job_types:
        hard.append(f"Job type {job.job_type.value} not in preferences")
    hard.extend(avoid_reasons(job, prefs))  # Java roles out when you skip Java
    relaxable.extend(off_focus_reasons(job, prefs))  # not an AI / Python role
    relaxable.extend(duration_reasons(job, prefs))  # a 6-month internship when you want at most 2 months

    if job.deadline_date and job.deadline_date < datetime.now(UTC).date():
        hard.append("Application deadline has passed")

    if prefs.get("verified_companies_only") and getattr(job, "company_verdict", None) == "unverified" \
            and not getattr(job, "company_tier", None):
        from app.services.company_verifier import is_trusted

        if not is_trusted(job, prefs):  # a company you marked legit is always in
            relaxable.append(f"{job.company_name} isn't a verified company or startup (you chose good companies only)")

    if prefs.get("skip_suspicious_companies", True) and getattr(job, "company_verdict", None) == "suspicious":
        from app.services.company_verifier import is_trusted

        if not is_trusted(job, prefs):  # you can mark a company legit
            flags = [r.removeprefix("⚠ ") for r in (getattr(job, "company_check", None) or {}).get("reasons") or []
                     if r.startswith("⚠")]
            hard.append("Possible fraud: " + ("; ".join(flags[:2]) or "the company check flagged this posting"))

    if prefs.get("exclude_no_sponsorship"):
        sponsorship = str((job.raw_data or {}).get("sponsorship") or "")
        if NO_SPONSORSHIP_PATTERNS.search(f"{sponsorship} {job.description[:6000]}"):
            hard.append("The posting says it does not sponsor visas")

    focus = get_focus(prefs)
    if focus is not None and focus.share >= 100 and location_tier(job.location, job.is_remote, focus) == TIER_ABROAD:
        hard.append(f"Outside {focus.country.title()} ({job.location or 'abroad'}): you chose {focus.country.title()} only")

    season = get_season(prefs)
    if season is not None and _is_internship(job):
        state, note = season_status(job.role_title, job.description, (job.raw_data or {}).get("terms"), season)
        if state == "conflict" and note:
            hard.append(note)
        elif state in ("other_mentioned", "immediate") and note:
            soft.append(note)

    remote_pref = prefs.get("remote_preference") or "any"
    if remote_pref == "remote" and not job.is_remote and "remote" not in text:
        soft.append("Not a remote role")

    posted_within = prefs.get("posted_within_days")
    if posted_within and job.posted_date and job.posted_date < (datetime.now(UTC).date() - timedelta(days=int(posted_within))):
        soft.append(f"Posted more than {posted_within} days ago")

    targets = [normalize_text(t) for t in (prefs.get("target_roles") or []) if t]
    if targets:
        from app.scrapers.base import related_role  # "AI/ML Intern" is an "AI Engineer Intern", as the scan judged it

        title = normalize_text(job.role_title)
        if not any(_role_matches(t, title) or related_role(t, title, interns_checked=True) for t in targets):
            soft.append("Title does not match your target roles")
    return hard, relaxable, soft


def _is_internship(job: Job) -> bool:
    return (job.job_type is not None and job.job_type.value == "internship") or bool(
        re.search(r"\bintern(ship)?\b|co-?op", job.role_title or "", re.IGNORECASE))


def prefilter(job: Job, prefs: dict[str, Any], strict: bool = True) -> tuple[bool, str | None]:
    """Cheap preference checks applied before any scoring. Returns (keep, reason_if_rejected).

    ``strict=False`` (Swipe Review) only rejects on hard reasons; soft ones are left to you.
    """
    hard, soft = filter_reasons(job, prefs)
    if hard:
        return False, hard[0]
    if strict and soft:
        return False, soft[0]
    return True, None


def _role_matches(target: str, title: str) -> bool:
    if target in title:
        return True
    target_tokens = [t for t in re.findall(r"[a-z0-9+#]+", target) if t not in {"of", "and", "the", "i", "ii", "iii"}]
    title_tokens = set(re.findall(r"[a-z0-9+#]+", title))
    synonyms = {"engineer": {"developer", "engineering", "swe"}, "developer": {"engineer"}, "intern": {"internship", "co-op", "coop"}}
    hits = 0
    for tok in target_tokens:
        if tok in title_tokens or synonyms.get(tok, set()) & title_tokens:
            hits += 1
    return bool(target_tokens) and hits / len(target_tokens) >= 0.67


# --------------------------------------------------------------------------- scoring
def _location_score(job: Job, prefs: dict[str, Any]) -> tuple[int, str]:
    focus = get_focus(prefs)
    if focus is not None:  # e.g. India first, Delhi NCR the prime location
        tier = location_tier(job.location, job.is_remote, focus)
        country = focus.country.title()
        if tier == TIER_PRIME:
            return 20, "in your prime location"
        if tier == TIER_COUNTRY:
            return 17, f"in {country}"
        if tier == TIER_ABROAD:
            return 4, f"outside {country}"
        return 13, "remote / location not stated"
    remote_pref = prefs.get("remote_preference") or "any"
    job_loc = normalize_text(job.location or "")
    is_remote = job.is_remote or "remote" in job_loc
    targets = [normalize_text(t) for t in (prefs.get("target_locations") or []) if t]
    loc_hit = any(t and (t in job_loc or (job_loc and job_loc.split(",")[0] in t)) for t in targets)
    if is_remote and remote_pref in ("remote", "hybrid", "any"):
        return 20, "remote-friendly"
    if loc_hit:
        return 20 if remote_pref != "remote" else 12, "location matches"
    if not targets:
        return 15, "no location preference"
    if remote_pref == "any":
        return 10, "location differs"
    return 5, "location outside preferences"


def _compensation_score(job: Job, prefs: dict[str, Any]) -> int:
    wanted_min = prefs.get("salary_min")
    if not wanted_min or not (job.salary_max or job.salary_min):
        return 15
    top = job.salary_max or job.salary_min or 0
    if top >= int(wanted_min):
        return 20
    return max(0, int(20 * top / int(wanted_min)) - 4)


def heuristic_evaluation(resume_content: dict[str, Any], job: Job, prefs: dict[str, Any], threshold: int) -> dict[str, Any]:
    resume = ResumeContent.model_validate(resume_content)
    required = job_skills(job)
    have = resume_skill_set(resume)
    matched = [s for s in required if s in have]
    missing = [s for s in required if s not in have]
    text = job_text(job)

    if required:
        coverage = len(matched) / len(required)
        skills_score = round(20 * min(1.0, coverage * 1.15))
    else:
        skills_score = round(10 + 10 * min(1.0, keyword_overlap(resume.full_text(), text) * 2))

    years = estimate_years_experience(resume)
    required_years = years_of_experience_required(text)
    level = job.experience_level.value if job.experience_level else None
    title = normalize_text(job.role_title)
    if level == "internship" or "intern" in title:
        experience_score = 20 if years < 3 else 14
    elif required_years is not None:
        experience_score = 20 if years >= required_years else max(4, round(20 * years / max(required_years, 1)))
    elif "senior" in title or "staff" in title or "lead" in title or level in ("senior", "lead"):
        experience_score = 20 if years >= 5 else max(6, round(20 * years / 5))
    else:
        experience_score = 16 if years >= 1 else 12

    industry_score = round(20 * min(1.0, keyword_overlap(resume.full_text(), text) * 2.5))
    location_score, location_note = _location_score(job, prefs)
    compensation_score = _compensation_score(job, prefs)
    total = skills_score + experience_score + industry_score + location_score + compensation_score

    strong = [display_skill(s) for s in matched[:6]]
    reasoning = (
        f"Matches {len(matched)} of {len(required) or 'n/a'} detected skills"
        f"{' (' + ', '.join(strong[:4]) + ')' if strong else ''}; "
        f"~{years:g} yrs experience{f' vs {required_years}+ required' if required_years else ''}; {location_note}."
    )
    return {
        "match_score": total,
        "skills_match": skills_score,
        "experience_match": experience_score,
        "industry_match": industry_score,
        "location_match": location_score,
        "compensation_match": compensation_score,
        "proceed_with_application": total >= threshold,
        "reasoning": reasoning,
        "missing_skills": [display_skill(s) for s in missing[:12]],
        "strong_matches": strong,
        "method": "heuristic",
    }


def _clamp(value: Any, lo: int = 0, hi: int = 20) -> int:
    try:
        return max(lo, min(hi, round(float(value))))
    except (TypeError, ValueError):
        return lo


def llm_evaluation(resume_content: dict[str, Any], job: Job, prefs: dict[str, Any], threshold: int) -> dict[str, Any]:
    salary = "not stated"
    if job.salary_min or job.salary_max:
        salary = f"{job.salary_min or '?'} - {job.salary_max or '?'} {job.salary_currency or ''}".strip()
    prompt = render_prompt(
        "job_evaluation",
        threshold=threshold,
        user_preferences_json={k: prefs.get(k) for k in (
            "target_roles", "target_locations", "remote_preference", "salary_min", "salary_max",
            "experience_level", "industries", "companies_to_avoid", "companies_to_target", "job_types",
            "location_focus", "internship_season", "focus_skills", "avoid_skills")},
        master_resume_json=resume_content,
        company_name=job.company_name,
        role_title=job.role_title,
        location=f"{job.location or 'unspecified'}{' (remote)' if job.is_remote else ''}",
        salary=salary,
        job_description_text=truncate(job_text(job), 14000),
    )
    data = get_llm().complete_json(prompt, schema=llm_schemas.JOB_EVALUATION_SCHEMA, effort="low", task="job_evaluation")
    ev = data.get("evaluation", data)
    result = {key: _clamp(ev.get(key)) for key in SCORE_KEYS}
    result["match_score"] = sum(result[k] for k in SCORE_KEYS)
    result["reasoning"] = str(ev.get("reasoning") or "").strip()
    result["missing_skills"] = [str(s) for s in (ev.get("missing_skills") or [])][:15]
    result["strong_matches"] = [str(s) for s in (ev.get("strong_matches") or [])][:10]
    result["proceed_with_application"] = bool(ev.get("proceed_with_application")) and result["match_score"] >= threshold
    result["method"] = "llm"
    return result


def evaluate_match(
    resume_content: dict[str, Any], job: Job, prefs: dict[str, Any], threshold: int | None = None, use_llm: bool = True
) -> dict[str, Any]:
    threshold = int(threshold if threshold is not None else prefs.get("auto_apply_threshold") or 80)
    llm = get_llm()
    if use_llm and llm.available:
        try:
            return llm_evaluation(resume_content, job, prefs, threshold)
        except LLMError as exc:
            logger.warning("LLM job evaluation failed for %s; using heuristic: %s", job.id, exc)
    return heuristic_evaluation(resume_content, job, prefs, threshold)


def priority_key(match_score: int | None, deadline: date | None) -> tuple[int, date]:
    """Sort key: match_score DESC, deadline ASC (behavioral rule #8)."""
    return (-(match_score or 0), deadline or date.max)
