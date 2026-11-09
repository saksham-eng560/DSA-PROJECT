"""Cover letter generation (PLAN.md §8 TASK: COVER LETTER GENERATION)."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.job import Job
from app.schemas.resume_content import ResumeContent
from app.services import llm_schemas
from app.services.job_matcher import job_skills, job_text
from app.services.llm import LLMError, get_llm, render_prompt
from app.services.text_utils import canonical_skill, display_skill, extract_skills, normalize_text, truncate

logger = logging.getLogger(__name__)

FORMAL_HINTS = ("bank", "capital", "financial", "law", "legal", "insurance", "government", "compliance", "audit")
STARTUP_HINTS = ("startup", "seed", "series a", "series b", "fast-paced", "early-stage", "founding", "scrappy")


def detect_tone(job: Job) -> str:
    text = normalize_text(f"{job.company_name} {job.description[:4000]}")
    if any(h in text for h in FORMAL_HINTS):
        return "formal"
    if any(h in text for h in STARTUP_HINTS):
        return "innovative"
    return "balanced"


def _company_hook(job: Job) -> str:
    """Pick a sentence from the JD that says something about the company / mission."""
    sentences = re.split(r"(?<=[.!?])\s+", job.description[:3000])
    for sentence in sentences:
        s = sentence.strip()
        if 40 <= len(s) <= 220 and re.search(r"\b(mission|we are|we're|our goal|we build|we help|founded|customers)\b", s, re.I):
            return s
    return ""


def heuristic_cover_letter(resume_content: dict[str, Any], job: Job) -> str:
    resume = ResumeContent.model_validate(resume_content)
    name = resume.personal_info.name or "the candidate"
    required = job_skills(job)
    have = {canonical_skill(s) for s in resume.skills.all()} | set(extract_skills(resume.full_text()))
    matched = [display_skill(s) for s in required if s in have][:4]
    tone = detect_tone(job)

    bullets: list[tuple[int, str, str]] = []
    for exp in resume.experience:
        for bullet in exp.bullets:
            score = sum(1 for s in extract_skills(bullet) if s in required) * 2 + (1 if re.search(r"\d", bullet) else 0)
            bullets.append((score, bullet.rstrip("."), exp.company))
    bullets.sort(key=lambda b: b[0], reverse=True)
    top = bullets[:2]

    hook = _company_hook(job)
    opener = {
        "formal": f"I am writing to apply for the {job.role_title} position at {job.company_name}.",
        "innovative": f"I'm excited to apply for the {job.role_title} role at {job.company_name}.",
        "balanced": f"I am excited to apply for the {job.role_title} role at {job.company_name}.",
    }[tone]
    p1 = opener
    if hook:
        p1 += f" What stood out to me in the role description is this: \"{hook}\" That is exactly the kind of work I want to contribute to."
    else:
        p1 += f" The role's focus on {', '.join(matched[:2]) or 'building great products'} aligns closely with what I do best."

    if top:
        achievements = " ".join(f"At {company}, I {b[0].lower() + b[1:] if b else b}." for _, b, company in top)
        p2 = f"{achievements}"
        if matched:
            p2 += f" This experience maps directly to your need for {', '.join(matched)}."
    else:
        p2 = f"My background in {', '.join(matched) or 'the core skills this role requires'} has prepared me to contribute quickly."

    p3 = (
        f"I would welcome the opportunity to bring this experience to {job.company_name} and to learn from your team. "
        "Thank you for your time and consideration — I look forward to discussing how I can help."
    )
    return f"Dear Hiring Team,\n\n{p1}\n\n{p2}\n\n{p3}\n\nSincerely,\n{name}"


def generate_cover_letter(resume_content: dict[str, Any], job: Job, company_research: str = "") -> dict[str, Any]:
    resume = ResumeContent.model_validate(resume_content)
    llm = get_llm()
    if llm.available:
        try:
            data = llm.complete_json(
                render_prompt(
                    "cover_letter",
                    candidate_name=resume.personal_info.name or "the candidate",
                    role_title=job.role_title,
                    company_name=job.company_name,
                    job_description_text=truncate(job_text(job), 10000),
                    resume_json=resume.to_dict(),
                    company_research=company_research or "(none — rely on the job description)",
                ),
                schema=llm_schemas.COVER_LETTER_SCHEMA,
                effort="medium",
                task="cover_letter",
            )
            letter = str(data.get("cover_letter") or "").strip()
            if len(letter.split()) >= 80:
                return {"cover_letter": letter, "tone": data.get("tone") or detect_tone(job), "method": "llm"}
        except LLMError as exc:
            logger.warning("LLM cover letter failed for job %s: %s", job.id, exc)
    letter = heuristic_cover_letter(resume.to_dict(), job)
    return {"cover_letter": letter, "tone": detect_tone(job), "method": "heuristic"}
