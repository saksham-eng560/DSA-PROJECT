"""Resume tailoring with a hard truthfulness guard (PLAN.md §8, DIRECTIVE 1).

The LLM may reorder, emphasise and rephrase. After generation, :func:`enforce_truthfulness`
re-validates the output against the master resume and reverts anything it cannot verify:

* personal info, education and certifications are copied verbatim from the master;
* every master job is kept with its original company / title / dates / location;
* bullets that introduce numbers or skills absent from the master entry are dropped;
* skills and project technologies must be evidenced somewhere in the master resume.
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

from app.models.job import Job
from app.schemas.resume_content import ResumeContent, normalize_resume
from app.services import llm_schemas
from app.services.job_matcher import job_skills, job_text
from app.services.llm import LLMError, get_llm, render_prompt
from app.services.text_utils import (
    STOPWORDS,
    canonical_skill,
    display_skill,
    extract_skills,
    normalize_text,
    tokenize,
    truncate,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- truthfulness
def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")}


def is_skill_supported(skill: str, master_text_norm: str, master_skills: set[str]) -> bool:
    canon = canonical_skill(skill)
    if not canon:
        return False
    if canon in master_skills:
        return True
    if re.search(rf"(?<![a-z0-9+#]){re.escape(canon)}(?![a-z0-9+#])", master_text_norm):
        return True
    # Implicit skill: every significant token must be evidenced (e.g. "REST API Design" <- "REST APIs").
    tokens = [t for t in re.findall(r"[a-z0-9+#]+", canon) if t not in STOPWORDS and t not in {"design", "development", "engineering"}]
    if not tokens:
        return False
    master_tokens = set(re.findall(r"[a-z0-9+#]+", master_text_norm))
    stems = {t.rstrip("s") for t in master_tokens}
    return all(t in master_tokens or t.rstrip("s") in stems for t in tokens)


def enforce_truthfulness(master: dict[str, Any], tailored: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    master_rc = ResumeContent.model_validate(master)
    tailored_rc = ResumeContent.model_validate(tailored)
    master_text_norm = normalize_text(master_rc.full_text())
    from app.services.job_matcher import resume_skill_set

    master_skills = resume_skill_set(master_rc)
    violations: list[str] = []
    out = copy.deepcopy(tailored_rc.to_dict())

    # Immutable sections
    out["personal_info"] = master_rc.personal_info.model_dump()
    out["education"] = [e.model_dump() for e in master_rc.education]
    out["certifications"] = [c.model_dump() for c in master_rc.certifications]
    out["awards"] = list(master_rc.awards)

    # Experience: keep all master jobs, verify each bullet
    def key(company: str, title: str) -> str:
        return f"{normalize_text(company)}|{normalize_text(title)}"

    tailored_by_key = {key(e.company, e.title): e for e in tailored_rc.experience}
    tailored_by_company = {normalize_text(e.company): e for e in tailored_rc.experience}
    ordered_keys = [key(e.company, e.title) for e in tailored_rc.experience]
    new_experience: list[dict[str, Any]] = []
    master_by_key = {key(e.company, e.title): e for e in master_rc.experience}
    ordering = [k for k in ordered_keys if k in master_by_key] + [k for k in master_by_key if k not in ordered_keys]
    for k in ordering:
        m = master_by_key[k]
        t = tailored_by_key.get(k) or tailored_by_company.get(normalize_text(m.company))
        entry = m.model_dump()
        if t is not None:
            m_text = " ".join(m.bullets)
            m_numbers = _numbers(m_text)
            m_skill_text = normalize_text(m_text + " " + master_text_norm)
            kept: list[str] = []
            for bullet in t.bullets:
                new_numbers = _numbers(bullet) - m_numbers
                if new_numbers:
                    violations.append(f"Dropped bullet with unverified metric(s) {sorted(new_numbers)} at {m.company}")
                    continue
                unsupported = [s for s in extract_skills(bullet) if not is_skill_supported(s, m_skill_text, master_skills)]
                if unsupported:
                    violations.append(f"Dropped bullet claiming unsupported skill(s) {unsupported} at {m.company}")
                    continue
                kept.append(bullet)
            # Never lose content: re-append master bullets whose facts were not carried over.
            kept_text = normalize_text(" ".join(kept))
            for mb in m.bullets:
                mb_numbers = _numbers(mb)
                if mb_numbers and not mb_numbers <= _numbers(kept_text):
                    kept.append(mb)
            entry["bullets"] = kept or list(m.bullets)
        elif k not in ordered_keys:
            violations.append(f"Restored omitted experience entry: {m.title} at {m.company}")
        new_experience.append(entry)
    out["experience"] = new_experience

    # Skills
    for bucket in ("technical", "languages", "tools", "soft_skills"):
        kept_skills = []
        for skill in out["skills"].get(bucket, []):
            if is_skill_supported(skill, master_text_norm, master_skills):
                kept_skills.append(skill)
            else:
                violations.append(f"Removed unsupported skill '{skill}'")
        out["skills"][bucket] = kept_skills
    # Never drop master skills entirely
    existing = {canonical_skill(s) for b in out["skills"].values() for s in b}
    for bucket in ("technical", "languages", "tools", "soft_skills"):
        for skill in getattr(master_rc.skills, bucket):
            if canonical_skill(skill) not in existing:
                out["skills"][bucket].append(skill)
                existing.add(canonical_skill(skill))

    # Projects: must exist in master; keep master URL; technologies verified
    master_projects = {normalize_text(p.name): p for p in master_rc.projects}
    projects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for proj in tailored_rc.projects:
        mp = master_projects.get(normalize_text(proj.name))
        if mp is None:
            violations.append(f"Removed project not present in master resume: '{proj.name}'")
            continue
        seen.add(normalize_text(mp.name))
        techs = [t for t in proj.technologies if is_skill_supported(t, master_text_norm, master_skills)]
        desc = proj.description if not (_numbers(proj.description) - _numbers(mp.description)) else mp.description
        projects.append({"name": mp.name, "description": desc or mp.description, "technologies": techs or mp.technologies, "url": mp.url})
    for name, mp in master_projects.items():
        if name not in seen:
            projects.append(mp.model_dump())
    out["projects"] = projects

    # Summary: revert if it claims unsupported skills or new numbers
    summary = out.get("summary") or ""
    bad = [s for s in extract_skills(summary) if not is_skill_supported(s, master_text_norm, master_skills)]
    if bad or (_numbers(summary) - _numbers(master_rc.full_text())):
        violations.append(f"Reverted summary containing unverified claims {bad or 'numbers'}")
        out["summary"] = master_rc.summary

    return normalize_resume(out), violations


# --------------------------------------------------------------------------- tailoring
def heuristic_tailor(master: dict[str, Any], job: Job) -> tuple[dict[str, Any], list[str]]:
    resume = ResumeContent.model_validate(master)
    jd = job_text(job)
    jd_tokens = set(tokenize(jd))
    jd_skills = job_skills(job)
    changes: list[str] = []

    def relevance(text: str) -> float:
        toks = tokenize(text)
        if not toks:
            return 0.0
        skill_hits = sum(1 for s in extract_skills(text) if s in jd_skills)
        return skill_hits * 3 + sum(1 for t in toks if t in jd_tokens) / len(toks)

    out = resume.to_dict()
    for exp in out["experience"]:
        original = list(exp["bullets"])
        exp["bullets"] = sorted(original, key=relevance, reverse=True)
        if exp["bullets"] != original:
            changes.append(f"Reordered bullets at {exp['company']} to lead with the most relevant achievements")

    for bucket in ("technical", "tools", "languages"):
        original = list(out["skills"][bucket])
        ranked = sorted(original, key=lambda s: (canonical_skill(s) not in jd_skills, original.index(s)))
        if ranked != original:
            out["skills"][bucket] = ranked
            changes.append(f"Moved {bucket} skills required by the job to the front")

    original_projects = [p["name"] for p in out["projects"]]
    out["projects"] = sorted(out["projects"], key=lambda p: relevance(p["description"] + " " + " ".join(p["technologies"])), reverse=True)
    if [p["name"] for p in out["projects"]] != original_projects:
        changes.append("Reordered projects by relevance to the role")

    matched = [s for s in jd_skills if s in {canonical_skill(x) for x in resume.skills.all()} or s in normalize_text(resume.full_text())]
    if resume.summary and matched:
        focus = ", ".join(display_skill(m) for m in matched[:4])
        out["summary"] = f"{resume.summary.rstrip('.')}. Targeting the {job.role_title} role with hands-on experience in {focus}."
        changes.append("Extended summary to reference the target role and matching skills")
    elif not resume.summary and matched:
        out["summary"] = f"Candidate for {job.role_title} with experience in {', '.join(display_skill(m) for m in matched[:5])}."
        changes.append("Added a targeted summary built from existing skills")
    return normalize_resume(out), changes


def tailor_resume(master: dict[str, Any], job: Job) -> dict[str, Any]:
    """Return {"tailored_resume", "changes_made", "violations", "method"}."""
    master = normalize_resume(master)
    llm = get_llm()
    if llm.available:
        try:
            data = llm.complete_json(
                render_prompt(
                    "resume_tailor",
                    company_name=job.company_name,
                    role_title=job.role_title,
                    job_description_text=truncate(job_text(job), 14000),
                    master_resume_json=master,
                ),
                schema=llm_schemas.TAILORED_RESUME_SCHEMA,
                effort="medium",
                task="resume_tailor",
            )
            tailored, violations = enforce_truthfulness(master, data.get("tailored_resume") or {})
            changes = [str(c) for c in (data.get("changes_made") or [])]
            return {"tailored_resume": tailored, "changes_made": changes, "violations": violations, "method": "llm"}
        except (LLMError, ValueError, TypeError) as exc:  # ValueError: a reply that isn't a resume (pydantic)
            logger.warning("LLM tailoring failed for job %s; using heuristic: %s", job.id, exc)
    tailored, changes = heuristic_tailor(master, job)
    tailored, violations = enforce_truthfulness(master, tailored)
    return {"tailored_resume": tailored, "changes_made": changes, "violations": violations, "method": "heuristic"}


def light_tailor(master: dict[str, Any], job: Job) -> dict[str, Any]:
    """Minor, word-for-word-safe tweaks: nothing you wrote is reworded or removed, only reordered.

    Bullets, projects and skills that match the job move to the top so a recruiter skimming the
    first lines sees the relevant work; the summary, titles, dates and every sentence stay yours.
    """
    master = normalize_resume(master)
    tailored = copy.deepcopy(master)
    wanted = {canonical_skill(s) for s in job_skills(job)}
    job_tokens = set(tokenize(job_text(job))) - STOPWORDS

    def relevance(text: str) -> float:
        skills = {canonical_skill(s) for s in extract_skills(text)}
        return 3 * len(skills & wanted) + 0.2 * len(set(tokenize(text)) & job_tokens)

    changes: list[str] = []
    for exp in tailored["experience"]:
        ordered = sorted(exp["bullets"], key=relevance, reverse=True)  # stable: ties keep your order
        if ordered != exp["bullets"]:
            exp["bullets"] = ordered
            changes.append(f"Led {exp['company'] or exp['title']} with the bullets closest to the role")
    projects = sorted(tailored["projects"], reverse=True,
                      key=lambda p: relevance(" ".join([p["name"], p["description"], *p["technologies"]])))
    if [p["name"] for p in projects] != [p["name"] for p in tailored["projects"]]:
        tailored["projects"] = projects
        changes.append(f"Moved '{projects[0]['name']}' to the top of Projects")
    technical = tailored["skills"]["technical"]
    ordered_skills = sorted(technical, key=lambda s: canonical_skill(s) not in wanted)
    if ordered_skills != technical:
        tailored["skills"]["technical"] = ordered_skills
        matched = [s for s in ordered_skills if canonical_skill(s) in wanted][:5]
        changes.append(f"Listed the skills this job asks for first ({', '.join(matched)})")
    return {"tailored_resume": tailored, "changes_made": changes, "violations": [], "method": "light"}
