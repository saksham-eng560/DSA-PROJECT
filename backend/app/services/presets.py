"""The one preset: "AI + Python · 2nd year".

AI engineer / GenAI / LLM internships first, then Python + FastAPI backend ones; at most 2 months; open to a
2nd-year student; at startups and good companies; in Delhi NCR first, then the rest of India and remote roles,
nothing from abroad. Applying it *replaces* your search filters with exactly these (no leftovers from earlier
settings). Your account, resume, saved answers, trusted / avoided companies and notification settings stay.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User, merge_preferences

PRESET = "ai-python"
PRESETS = (PRESET,)

AI_PYTHON_ROLES = ["AI Engineer Intern", "Generative AI Intern", "AI Developer Intern", "Python Developer Intern",
                   "Backend Developer Intern", "FastAPI Developer Intern", "Software Engineer Intern"]
AI_PYTHON_FOCUS_SKILLS = ["AI", "LLMs", "Generative AI", "AI agents", "RAG", "Machine Learning", "NLP", "Deep Learning",
                          "Python", "FastAPI", "Django", "Flask", "LangChain"]
# Other backend stacks: a posting asking for one of these and none of yours is left out.
AVOID_SKILLS = ["Java", "Spring Boot", "PHP", "Laravel", ".NET", "C#", "Node.js", "Golang", "Ruby on Rails"]
EXCLUDED_TITLES = [
    "Java", "Spring Boot", "PHP", ".NET", "Node.js", "Frontend", "Front End", "Front-End", "React Developer",
    "Android", "iOS", "Flutter", "React Native", "Data Science", "Data Scientist", "Data Analyst", "Data Analytics",
    "Business Analyst", "DevOps", "QA", "Quality Assurance", "Tester", "UI/UX", "Designer", "Graphic Design",
    "Marketing", "Sales", "Business Development", "Human Resources", "HR", "Content Writing", "Customer Support",
]
DELHI_NCR = ["Delhi", "New Delhi", "Delhi NCR", "Gurugram", "Gurgaon", "Noida", "Greater Noida", "Faridabad",
             "Ghaziabad"]
# Internshala, LinkedIn, Naukri, Unstop and Indeed India, Wellfound for startups, and remote boards for roles open to
# India. The big tech / top Indian startup / AI company boards are searched too (scan_top_companies). The US internship
# lists and US startup boards are not: almost everything there is on-site in the US.
PLATFORMS = ["internshala", "linkedin", "naukri", "unstop", "indeed", "wellfound", "remote_boards"]
# What the preset used to search: settings saved with exactly these are upgraded to today's sources, once.
EARLIER_PLATFORMS = (["internshala", "linkedin", "indeed", "wellfound"],)
_OFF_FOCUS_SEARCH = re.compile(r"java(?!script)|data-scien|data-analy|business-analy|front-end|android|mobile-app|"
                               r"ui-ux|marketing|finance|web-development", re.I)


def upgrade_earlier_platforms(db: Session) -> int:
    """Settings saved before the preset gained Naukri, Unstop and remote boards, with exactly its earlier sources,
    search today's preset sources. Done once per account (``platforms_upgraded``): if you later choose those same
    4 sources yourself, they stay yours. Returns how many accounts were upgraded."""
    upgraded = 0
    for user in db.scalars(select(User)).all():
        saved = dict(user.preferences or {})
        if saved.get("platforms_upgraded"):
            continue
        if saved.get("platforms") in EARLIER_PLATFORMS:
            saved["platforms"] = list(PLATFORMS)
            upgraded += 1
        user.preferences = {**saved, "platforms_upgraded": True}
    db.flush()
    return upgraded


def apply_preset(prefs: dict[str, Any], name: str = PRESET) -> dict[str, Any]:
    """Return your preferences with the search set to exactly the preset."""
    if name not in PRESETS:
        raise ValueError(f"Unknown preset '{name}' (there is one: {PRESET})")
    current = merge_preferences(prefs, None)
    sources = copy.deepcopy(current.get("sources") or {})
    sources.update({
        "internshala_urls": [u for u in sources.get("internshala_urls") or [] if not _OFF_FOCUS_SEARCH.search(u)],
        "internship_lists": [],  # SimplifyJobs / vanshb03: US internships
        "greenhouse_boards": [], "lever_companies": [], "ashby_boards": [], "workday_sites": [],  # US startup boards
    })
    return merge_preferences(current, {
        # what
        "target_roles": list(AI_PYTHON_ROLES),
        "focus_skills": list(AI_PYTHON_FOCUS_SKILLS),
        "avoid_skills": list(AVOID_SKILLS),
        "keywords_exclude": list(EXCLUDED_TITLES),  # replaced, not added to: no leftover filters
        "industries": [],
        "companies_to_target": [],
        # who: a 2nd-year student, internships of at most 2 months
        "internships_only": True,
        "job_types": ["internship"],
        "experience_level": ["internship"],
        "year_of_study": 2,
        "max_internship_months": 2,
        "internship_season": "Summer 2027",
        # where: Delhi NCR first, then India and remote; nothing from abroad
        "location_focus": {"enabled": True, "country": "India", "prime_cities": list(DELHI_NCR), "country_share": 100},
        "target_locations": ["Delhi, India", "India"],
        "remote_preference": "any",
        "timezone": "Asia/Kolkata",
        "exclude_no_sponsorship": False,
        "salary_min": None,
        "salary_max": None,
        # which companies: startups and good companies
        "verified_companies_only": True,
        "skip_suspicious_companies": True,
        "scan_top_companies": True,
        # sources and pace
        "platforms": list(PLATFORMS),
        "sources": sources,
        "posted_within_days": 30,
        "internshala_share": 50,
        "internshala_per_scan": 40,
        "source_share": 40,  # no one site more than 40 % of a scan while the others have postings
        "fill_deck_to": 40,  # a thin deck is filled with near misses (longer internships, unverified companies...)
        # applying: you keep a card, the agent fills and submits it (eligibility answers are never guessed)
        "review_mode": "swipe",
        "auto_submit_kept": True,
    })
