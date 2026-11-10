"""Per-user job search: suggestions as you type (trie) and ranked full-text results (BM25).

For each user the service keeps two structures over their jobs:

* a :class:`~app.dsa.trie.Trie` of role titles, companies, skills and locations, weighted by how
  often each occurs. Every word of a phrase is a way in, so typing "back" finds
  "Python Backend Intern";
* an :class:`~app.dsa.inverted_index.InvertedIndex` over title, company, location, skills and
  the start of the description, with the title weighted highest.

Building them reads the user's jobs once. The pair is kept in an
:class:`~app.dsa.lru_cache.LRUCache` keyed by user and rebuilt only when the user's job count or
newest job changes, so typing in the search box never touches the description text again.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.dsa.inverted_index import InvertedIndex
from app.dsa.lru_cache import LRUCache
from app.dsa.trie import Trie
from app.models.application import Application
from app.models.job import Job
from app.models.user import User
from app.services.text_utils import display_skill, normalize_text

MAX_INDEXED_JOBS = 5000
DESCRIPTION_CHARS = 1500
FIELD_WEIGHTS = {"title": 4.0, "company": 3.0, "skills": 2.0, "location": 1.5, "description": 1.0}
KIND_ORDER = {"title": 0, "company": 1, "skill": 2, "location": 3}


@dataclass
class UserIndex:
    signature: tuple[Any, ...]
    trie: Trie
    index: InvertedIndex[str]
    build_ms: float
    job_count: int
    built_at: float = field(default_factory=time.time)


_indexes: LRUCache[str, UserIndex] = LRUCache(capacity=64, ttl=15 * 60)


def cache_stats() -> dict[str, Any]:
    return _indexes.stats()


def invalidate(user_id: uuid.UUID | str | None = None) -> None:
    if user_id is None:
        _indexes.clear()
    else:
        _indexes.delete(str(user_id))


def _signature(db: Session, user: User) -> tuple[Any, ...]:
    count, newest = db.execute(
        select(func.count(Job.id), func.max(Job.discovered_at))
        .join(Application, and_(Application.job_id == Job.id, Application.user_id == user.id))
        .where(Job.is_active.is_(True))
    ).one()
    return (count or 0, newest.isoformat() if newest else None)


def _add_phrase(trie: Trie, text: str | None, kind: str, weight: float = 1.0) -> None:
    """Insert ``text`` and each of its word-suffixes, all pointing back at the full phrase."""
    text = " ".join((text or "").split())
    if not text or len(text) > 120:
        return
    payload = {"text": text, "kind": kind}
    words = normalize_text(text).split(" ")
    trie.insert(" ".join(words), weight, payload)
    for start in range(1, min(len(words), 6)):
        if len(words[start]) >= 2:
            trie.insert(" ".join(words[start:]), weight * 0.6, payload)


def build(db: Session, user: User) -> UserIndex:
    started = time.perf_counter()
    signature = _signature(db, user)
    rows = db.execute(
        select(Job.id, Job.role_title, Job.company_name, Job.location, Job.extracted_skills,
               func.substr(Job.description, 1, DESCRIPTION_CHARS))
        .join(Application, and_(Application.job_id == Job.id, Application.user_id == user.id))
        .where(Job.is_active.is_(True))
        .order_by(Job.discovered_at.desc())
        .limit(MAX_INDEXED_JOBS)
    ).all()
    trie, index = Trie(), InvertedIndex[str]()
    for job_id, title, company, location, skills, description in rows:
        skills = skills or []
        index.add(str(job_id), {"title": title or "", "company": company or "", "location": location or "",
                                "skills": " ".join(skills), "description": description or ""}, FIELD_WEIGHTS)
        _add_phrase(trie, title, "title")
        _add_phrase(trie, company, "company", 1.5)
        _add_phrase(trie, (location or "").split(",")[0], "location", 0.5)
        for skill in skills:
            _add_phrase(trie, display_skill(skill), "skill", 0.75)
    return UserIndex(signature, trie, index, round((time.perf_counter() - started) * 1000, 1), len(rows))


def get_index(db: Session, user: User) -> UserIndex:
    """The user's index from the LRU cache; rebuilt when their jobs changed."""
    key = str(user.id)
    cached: UserIndex | None = _indexes.get(key)
    if cached is not None and cached.signature == _signature(db, user):
        return cached
    built = build(db, user)
    _indexes.put(key, built)
    return built


def suggest(db: Session, user: User, prefix: str, limit: int = 8) -> list[dict[str, Any]]:
    """Completions for ``prefix``, heaviest first, one per distinct phrase."""
    prefix = normalize_text(prefix)
    if not prefix:
        return []
    user_index = get_index(db, user)
    seen: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    for _, weight, payload in user_index.trie.suggest(prefix, k=limit * 4):
        key = (payload["kind"], payload["text"].lower())
        if key in seen:
            continue
        seen.add(key)
        results.append({"text": payload["text"], "kind": payload["kind"], "weight": round(weight, 2)})
        if len(results) >= limit:
            break
    return results


def search(db: Session, user: User, query: str, limit: int = 20) -> dict[str, Any]:
    """Ranked job ids for ``query`` plus what the engine did (terms used, typos fixed, timing)."""
    started = time.perf_counter()
    user_index = get_index(db, user)
    explained = user_index.index.search_explained(query, k=limit)
    return {
        "hits": [(uuid.UUID(job_id), score) for job_id, score in explained["hits"]],
        "terms": explained["terms"],
        "corrections": explained["corrections"],
        "matched": explained["matched"],
        "took_ms": round((time.perf_counter() - started) * 1000, 2),
        "index": {**user_index.index.stats(), "trie": user_index.trie.stats(), "build_ms": user_index.build_ms,
                  "jobs": user_index.job_count},
    }
