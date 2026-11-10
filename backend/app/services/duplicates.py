"""Group the same job posted on several sites, with Union-Find.

The scan already merges postings whose company, title and city are *identical* (the
``dedupe_key`` index). What slips through is the same internship written slightly differently:
"SDE Intern" on LinkedIn, "Software Development Engineer Intern (Summer 2027)" on the company
board. Those are found here.

1. **Bucket by company** (a hash map on the normalised name), so only jobs at the same company
   are ever compared - this avoids comparing every job with every other job.
2. **Compare pairs inside a bucket**: titles count as the same job when their word sets overlap
   strongly (Jaccard) or their edit-distance similarity is high.
3. **Union** each matching pair. Matching is not transitive by itself (A~B and B~C does not make
   A~C look alike), but the *groups* are - that is exactly what Union-Find maintains.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.dsa.strings import similarity
from app.dsa.union_find import UnionFind
from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.services.text_utils import normalize_company, normalize_title

MAX_JOBS = 3000
MAX_BUCKET = 80  # a company with more postings than this is compared on its most recent ones
JACCARD_THRESHOLD = 0.8
SIMILARITY_THRESHOLD = 0.88
_EXPANSIONS = {"sde": "software development engineer", "swe": "software engineer", "ml": "machine learning",
               "ai": "artificial intelligence", "dev": "developer", "eng": "engineer", "intern": "internship",
               "fe": "frontend", "be": "backend", "front end": "frontend", "back end": "backend",
               "full stack": "fullstack"}
_NOISE = frozenset({"summer", "winter", "fall", "spring", "2025", "2026", "2027", "2028", "remote", "hybrid", "onsite",
                    "india", "the", "a", "an", "and", "of", "for", "in", "at", "-", "program", "programme"})


def title_key(title: str | None) -> str:
    """A title with abbreviations expanded and season / location noise removed."""
    text = normalize_title(title)
    for short, full in _EXPANSIONS.items():
        text = f" {text} ".replace(f" {short} ", f" {full} ").strip()
    return " ".join(word for word in text.split() if word not in _NOISE)


def same_role(a: str, b: str) -> bool:
    if a == b:
        return bool(a)
    words_a, words_b = set(a.split()), set(b.split())
    if not words_a or not words_b:
        return False
    jaccard = len(words_a & words_b) / len(words_a | words_b)
    return jaccard >= JACCARD_THRESHOLD or similarity(a, b) >= SIMILARITY_THRESHOLD


def cluster(jobs: list[dict[str, Any]]) -> tuple[list[list[dict[str, Any]]], dict[str, int]]:
    """``jobs`` need ``id``, ``company`` and ``title``. Returns ``(groups of 2+, counters)``."""
    dsu: UnionFind[Any] = UnionFind(job["id"] for job in jobs)
    buckets: dict[str, list[tuple[Any, str]]] = defaultdict(list)
    for job in jobs:
        company = normalize_company(job.get("company"))
        if company:
            buckets[company].append((job["id"], title_key(job.get("title"))))
    comparisons = unions = 0
    for members in buckets.values():
        members = members[:MAX_BUCKET]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                comparisons += 1
                if same_role(members[i][1], members[j][1]) and dsu.union(members[i][0], members[j][0]):
                    unions += 1
    by_id = {job["id"]: job for job in jobs}
    groups = [[by_id[job_id] for job_id in group] for group in dsu.groups(min_size=2)]
    counters = {"jobs": len(jobs), "companies": len(buckets), "comparisons": comparisons, "unions": unions,
                "groups": len(groups), "duplicates": sum(len(g) - 1 for g in groups),
                "all_pairs": len(jobs) * (len(jobs) - 1) // 2}
    return groups, counters


def find_duplicates(db: Session, user: User, limit: int = 50) -> dict[str, Any]:
    rows = db.execute(
        select(Job.id, Job.company_name, Job.role_title, Job.location, Job.source_platform, Job.source_url,
               Application.id, Application.status, Application.match_score)
        .join(Application, and_(Application.job_id == Job.id, Application.user_id == user.id))
        .where(Job.is_active.is_(True))
        .order_by(Job.discovered_at.desc())
        .limit(MAX_JOBS)
    ).all()
    jobs = [{"id": str(job_id), "company": company, "title": title, "location": location,
             "platform": platform.value if platform else None,
             "url": None if "manual.autoapply.invalid" in (url or "") else url,  # logged by hand, no link
             "application_id": str(app_id) if isinstance(app_id, uuid.UUID) else app_id,
             "status": status.value if status else None, "match_score": score}
            for job_id, company, title, location, platform, url, app_id, status, score in rows]
    groups, counters = cluster(jobs)
    return {
        "groups": [{"company": group[0]["company"], "title": group[0]["title"], "size": len(group),
                    "platforms": sorted({job["platform"] for job in group if job["platform"]}),
                    "jobs": group} for group in groups[:limit]],
        "stats": counters,
    }
