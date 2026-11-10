"""Insights computed with the project's own data structures and ML models.

Each function pairs one question the dashboard asks with the structure that answers it:

==========================  ====================================================================
``best_matches``            binary heap (top-K) + binary search (percentile of each score)
``score_summary``           quickselect (median) + binary search (quartiles, counts in a range)
``activity``                Fenwick tree (rolling 7- and 30-day totals, "day you reached N")
``interview_schedule``      interval sweep (clashes, free slots) + min-heap (overlap depth)
``job_clusters``            TF-IDF vectors + k-means clustering
``reply_model``             logistic regression trained on your own application history
``skill_gap``               skill graph: neighbours (transferable), BFS (the view), Dijkstra (learning path)
==========================  ====================================================================
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.dsa.dp import longest_increasing_subsequence
from app.dsa.fenwick import FenwickTree
from app.dsa.heap import top_k
from app.dsa.intervals import Interval, find_conflicts, free_slots, max_non_overlapping, merge_intervals, min_rooms
from app.dsa.sorting import count_in_range, median, percentile_rank, quickselect
from app.ml.kmeans import KMeans
from app.ml.logistic import LogisticRegression
from app.ml.tfidf import TfidfVectorizer, cosine
from app.models.application import Application
from app.models.enums import INTERVIEW_STATUSES, RESPONSE_STATUSES, SUBMITTED_STATUSES, ApplicationStatus
from app.models.interview import Interview
from app.models.job import Job
from app.models.resume import Resume
from app.models.user import User
from app.schemas.resume_content import ResumeContent
from app.services import skill_graph
from app.services.job_matcher import job_skills, resume_skill_set
from app.services.text_utils import display_skill

WAITING = (ApplicationStatus.DISCOVERED, ApplicationStatus.MATCHED)
MAX_ROWS = 4000


def _applications(db: Session, user: User, statuses: tuple[ApplicationStatus, ...] | None = None) -> list[Application]:
    query = select(Application).options(joinedload(Application.job)).where(Application.user_id == user.id)
    if statuses:
        query = query.where(Application.status.in_(statuses))
    return list(db.scalars(query.order_by(Application.created_at.desc()).limit(MAX_ROWS)).unique().all())


def _job_card(app: Application) -> dict[str, Any]:
    job = app.job
    return {"application_id": str(app.id), "job_id": str(job.id), "role_title": job.role_title,
            "company_name": job.company_name, "location": job.location, "is_remote": job.is_remote,
            "status": app.status.value, "match_score": app.match_score,
            "platform": job.source_platform.value if job.source_platform else None,
            "deadline_date": job.deadline_date.isoformat() if job.deadline_date else None}


# --------------------------------------------------------------------------- ranking
def _urgency(deadline: date | None, today: date) -> float:
    """0-10 bonus for a deadline within two weeks (a job closing tomorrow should not sit at the bottom)."""
    if deadline is None or deadline < today:
        return 0.0
    days = (deadline - today).days
    return max(0.0, 10.0 * (1 - days / 14)) if days <= 14 else 0.0


def best_matches(db: Session, user: User, k: int = 10) -> dict[str, Any]:
    """The K best waiting jobs by score plus deadline urgency, without sorting the whole deck."""
    apps = [a for a in _applications(db, user, WAITING) if a.review_decision is None and a.job.is_active]
    today = datetime.now(UTC).date()
    sorted_scores = sorted(a.match_score for a in apps if a.match_score is not None)

    def priority(app: Application) -> tuple[float, int]:
        return ((app.match_score or 0) + _urgency(app.job.deadline_date, today), app.match_score or 0)

    best = top_k(apps, k, key=priority)
    return {
        "items": [{**_job_card(app), "priority": round(priority(app)[0], 1),
                   "percentile": percentile_rank(sorted_scores, app.match_score) if app.match_score is not None else None}
                  for app in best],
        "considered": len(apps),
        "algorithm": {"name": "top-K with a size-K min-heap", "time": "O(n log k)",
                      "comparisons_saved_vs_sort": max(0, len(apps) - k)},
    }


def score_summary(db: Session, user: User) -> dict[str, Any]:
    scores = [a.match_score for a in _applications(db, user) if a.match_score is not None]
    if not scores:
        return {"count": 0}
    ordered = sorted(scores)
    n = len(scores)
    improving = longest_increasing_subsequence(scores[::-1])  # oldest first
    return {
        "count": n,
        "median": median(scores),
        "quartiles": [quickselect(scores, n // 4), quickselect(scores, n // 2), quickselect(scores, (3 * n) // 4)],
        "min": ordered[0], "max": ordered[-1],
        "bands": {"strong (80-100)": count_in_range(ordered, 80, 100), "good (60-79)": count_in_range(ordered, 60, 79),
                  "fair (40-59)": count_in_range(ordered, 40, 59), "weak (0-39)": count_in_range(ordered, 0, 39)},
        "longest_improving_run": len(improving),
    }


# --------------------------------------------------------------------------- activity
def activity(db: Session, user: User, days: int = 90) -> dict[str, Any]:
    """Daily applications with rolling totals, from one Fenwick tree."""
    days = max(7, min(days, 365))
    today = datetime.now(UTC).date()
    start = today - timedelta(days=days - 1)
    tree = FenwickTree(days)
    responses = FenwickTree(days)
    for app in _applications(db, user):
        if app.submitted_at and start <= app.submitted_at.date() <= today:
            tree.add((app.submitted_at.date() - start).days, 1)
        if app.first_response_at and start <= app.first_response_at.date() <= today:
            responses.add((app.first_response_at.date() - start).days, 1)
    series = []
    for index in range(days):
        series.append({
            "date": (start + timedelta(days=index)).isoformat(),
            "applied": int(tree.get(index)),
            "rolling_7": int(tree.range_sum(max(0, index - 6), index)),
            "rolling_30": int(tree.range_sum(max(0, index - 29), index)),
            "cumulative": int(tree.prefix_sum(index + 1)),
            "responses": int(responses.get(index)),
        })
    total = int(tree.total())
    milestones = []
    for target in (1, 10, 25, 50, 100, 250):
        if total >= target:
            milestones.append({"applications": target, "date": (start + timedelta(days=tree.lower_bound(target))).isoformat()})
    best_week = max(series, key=lambda day: day["rolling_7"]) if series else None
    return {
        "days": days, "series": series, "total_applied": total, "total_responses": int(responses.total()),
        "last_7": int(tree.range_sum(days - 7, days - 1)), "last_30": int(tree.range_sum(max(0, days - 30), days - 1)),
        "best_week": {"ending": best_week["date"], "applied": best_week["rolling_7"]} if best_week and total else None,
        "milestones": milestones,
        "algorithm": {"name": "Fenwick tree", "time": "O(log n) per update and per range sum"},
    }


# --------------------------------------------------------------------------- interviews
def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def interview_schedule(db: Session, user: User, days: int = 7) -> dict[str, Any]:
    """Clashing interviews, the busiest moment, and free one-hour slots in working hours."""
    now = datetime.now(UTC)
    horizon = now + timedelta(days=days)
    rows = db.scalars(
        select(Interview).join(Application, Application.id == Interview.application_id)
        .where(Application.user_id == user.id, Interview.scheduled_at >= now - timedelta(hours=4))
        .order_by(Interview.scheduled_at).limit(200)
    ).all()
    rows = [i for i in rows if (i.outcome or "") not in ("cancelled", "rescheduled")]

    def label(interview: Interview) -> dict[str, Any]:
        job = interview.application.job
        return {"id": str(interview.id), "company_name": job.company_name, "role_title": job.role_title,
                "scheduled_at": interview.scheduled_at.isoformat(), "duration_minutes": interview.duration_minutes or 60}

    intervals = [Interval(i.scheduled_at, i.scheduled_at + timedelta(minutes=i.duration_minutes or 60), label(i))
                 for i in rows]
    conflicts = [{"first": a.label, "second": b.label,
                  "overlap_minutes": int((min(a.end, b.end) - max(a.start, b.start)).total_seconds() // 60)}
                 for a, b in find_conflicts(intervals)]
    zone = _zone(user.prefs.get("timezone"))
    slots: list[dict[str, Any]] = []
    for offset in range(days):
        day = (now.astimezone(zone) + timedelta(days=offset)).date()
        if day.weekday() >= 5:
            continue
        window_start = max(now, datetime(day.year, day.month, day.day, 10, tzinfo=zone).astimezone(UTC))
        window_end = datetime(day.year, day.month, day.day, 18, tzinfo=zone).astimezone(UTC)
        if window_start >= window_end:
            continue
        for slot in free_slots(intervals, window_start, window_end, min_length=timedelta(hours=1)):
            slots.append({"start": slot.start.isoformat(), "end": slot.end.isoformat(),
                          "minutes": int((slot.end - slot.start).total_seconds() // 60)})
    upcoming = [i for i in intervals if i.start <= horizon]
    return {
        "interviews": [i.label for i in upcoming],
        "conflicts": conflicts,
        "busy_blocks": [{"start": b.start.isoformat(), "end": b.end.isoformat()} for b in merge_intervals(upcoming)],
        "most_at_once": min_rooms(upcoming),
        "attendable": len(max_non_overlapping(upcoming)),
        "free_slots": slots[:12],
        "timezone": str(zone),
        "algorithm": {"name": "sort + sweep, min-heap of end times", "time": "O(n log n)"},
    }


def conflicts_for(db: Session, user: User, start: datetime, minutes: int, ignore_id: Any = None) -> list[dict[str, Any]]:
    """Existing interviews that overlap a proposed one (used when an interview is created or moved)."""
    end = start + timedelta(minutes=minutes or 60)
    rows = db.scalars(
        select(Interview).join(Application, Application.id == Interview.application_id)
        .where(Application.user_id == user.id, Interview.scheduled_at < end,
               Interview.scheduled_at >= start - timedelta(hours=12))
    ).all()
    proposed = Interval(start, end, None)
    others = [Interval(i.scheduled_at, i.scheduled_at + timedelta(minutes=i.duration_minutes or 60), i)
              for i in rows if i.id != ignore_id and (i.outcome or "") not in ("cancelled", "rescheduled")]
    clashes = [b if a is proposed else a for a, b in find_conflicts([proposed, *others]) if proposed in (a, b)]
    return [{"id": str(c.label.id), "company_name": c.label.application.job.company_name,
             "role_title": c.label.application.job.role_title, "scheduled_at": c.label.scheduled_at.isoformat()}
            for c in clashes]


# --------------------------------------------------------------------------- clustering
def _job_document(job: Job) -> str:
    skills = " ".join(job.extracted_skills or [])
    return f"{job.role_title} {job.role_title} {skills} {skills} {(job.description or '')[:1200]}"


def job_clusters(db: Session, user: User, k: int = 5, limit: int = 400) -> dict[str, Any]:
    """Group the user's jobs into themes: TF-IDF vectors, then k-means."""
    apps = [a for a in _applications(db, user) if a.job.is_active][:limit]
    if len(apps) < 4:
        return {"clusters": [], "jobs": len(apps), "trained": False,
                "reason": "Needs at least 4 jobs. Run a scan first."}
    vectorizer = TfidfVectorizer(min_df=2 if len(apps) >= 12 else 1, max_features=1500)
    vectors = vectorizer.fit_transform([_job_document(a.job) for a in apps])
    model = KMeans(k=max(2, min(k, len(apps) // 2))).fit(vectors)
    clusters = []
    for cluster in range(len(model.centroids)):
        members = [(apps[i], vectors[i]) for i, label in enumerate(model.labels) if label == cluster]
        if not members:
            continue
        centroid = model.centroids[cluster]
        closest = top_k(members, 4, key=lambda member: cosine(member[1], centroid))
        scores = [a.match_score for a, _ in members if a.match_score is not None]
        skills = Counter(s for a, _ in members for s in (a.job.extracted_skills or []))
        clusters.append({
            "id": cluster,
            "label": " · ".join(display_skill(s) for s, _ in skills.most_common(3)) or " · ".join(model.top_terms(cluster, 3)),
            "terms": model.top_terms(cluster, 6),
            "size": len(members),
            "average_score": round(sum(scores) / len(scores), 1) if scores else None,
            "examples": [_job_card(a) for a, _ in closest],
        })
    clusters.sort(key=lambda c: -c["size"])
    return {"clusters": clusters, "jobs": len(apps), "trained": True, "iterations": model.iterations,
            "cohesion": round(model.cohesion(vectors), 3), "vocabulary": len(vectorizer.idf),
            "algorithm": {"name": "TF-IDF + k-means++", "time": "O(iterations * n * k * terms)"}}


def similar_jobs(db: Session, user: User, job_id: Any, limit: int = 5) -> list[dict[str, Any]]:
    """Jobs most like ``job_id`` by TF-IDF cosine similarity."""
    apps = [a for a in _applications(db, user) if a.job.is_active][:600]
    target = next((a for a in apps if a.job_id == job_id), None)
    if target is None or len(apps) < 2:
        return []
    vectorizer = TfidfVectorizer(max_features=2000)
    vectors = vectorizer.fit_transform([_job_document(a.job) for a in apps])
    anchor = vectors[apps.index(target)]
    scored = [(app, cosine(anchor, vector)) for app, vector in zip(apps, vectors, strict=True) if app is not target]
    return [{**_job_card(app), "similarity": round(score, 3)}
            for app, score in top_k(scored, limit, key=lambda pair: pair[1]) if score > 0.05]


# --------------------------------------------------------------------------- reply model
FEATURES = ("match score", "remote role", "well-known company", "company job board", "has deadline",
            "skills you match", "days listed")
_BOARD_PLATFORMS = {"greenhouse", "lever", "ashby", "workday"}
MIN_TRAINING_ROWS = 12
SETTLED_AFTER_DAYS = 14  # an application this old with no reply counts as "no reply"


def _features(app: Application, have: set[str], today: date) -> list[float]:
    job = app.job
    wanted = job_skills(job)
    matched = sum(1 for skill in wanted if skill in have)
    reference = (app.submitted_at.date() if app.submitted_at else today)
    listed = (reference - job.posted_date).days if job.posted_date else 7
    return [
        (app.match_score or 0) / 100.0,
        1.0 if job.is_remote else 0.0,
        1.0 if job.company_tier else 0.0,
        1.0 if (job.source_platform and job.source_platform.value in _BOARD_PLATFORMS) else 0.0,
        1.0 if job.deadline_date else 0.0,
        matched / len(wanted) if wanted else 0.5,
        min(max(listed, 0), 60) / 60.0,
    ]


def _resume_skills(db: Session, user: User) -> set[str]:
    master = db.scalar(select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True),
                                            Resume.is_active.is_(True)).order_by(Resume.updated_at.desc()))
    if master is None:
        return set()
    try:
        return resume_skill_set(ResumeContent.model_validate(master.parsed_content))
    except ValueError:
        return set()


def reply_model(db: Session, user: User, predict: int = 8) -> dict[str, Any]:
    """Train logistic regression on past applications (reply vs. no reply) and score the waiting jobs."""
    apps = _applications(db, user)
    have = _resume_skills(db, user)
    now = datetime.now(UTC)
    today = now.date()
    rows: list[list[float]] = []
    labels: list[int] = []
    for app in apps:
        if not (app.status in SUBMITTED_STATUSES or app.submitted_at):
            continue
        positive = app.status in INTERVIEW_STATUSES or (
            (app.status in RESPONSE_STATUSES or app.first_response_at is not None)
            and app.status != ApplicationStatus.REJECTED)
        settled = positive or app.status == ApplicationStatus.REJECTED or (
            app.submitted_at is not None and (now - app.submitted_at).days >= SETTLED_AFTER_DAYS)
        if settled:
            rows.append(_features(app, have, today))
            labels.append(1 if positive else 0)
    positives = sum(labels)
    base = {"features": list(FEATURES), "examples": len(rows), "positives": positives,
            "algorithm": {"name": "logistic regression (batch gradient descent, L2)",
                          "time": "O(epochs * n * features)"}}
    if len(rows) < MIN_TRAINING_ROWS or positives == 0 or positives == len(rows):
        return {**base, "trained": False,
                "reason": f"Needs at least {MIN_TRAINING_ROWS} settled applications with both replies and non-replies "
                          f"(have {len(rows)}, {positives} with a reply). It trains itself as your history grows."}
    model = LogisticRegression().fit(rows, labels)
    waiting = [a for a in apps if a.status in WAITING and a.review_decision is None and a.job.is_active]
    scored = [(app, _features(app, have, today)) for app in waiting]
    best = top_k(scored, predict, key=lambda pair: model.predict_proba(pair[1]))
    return {
        **base, "trained": True,
        "accuracy": round(model.accuracy(rows, labels), 3),
        "base_rate": round(positives / len(rows), 3),
        "weights": [{"feature": name, "weight": round(weight, 3)} for name, weight in zip(FEATURES, model.weights, strict=True)],
        "loss": {"start": round(model.loss_history[0], 4), "end": round(model.loss_history[-1], 4)},
        "predictions": [{**_job_card(app), "reply_chance": round(model.predict_proba(features), 3),
                         "why": model.explain(features, FEATURES)[:3]} for app, features in best],
    }


# --------------------------------------------------------------------------- skills
def skill_gap(db: Session, user: User, limit: int = 8) -> dict[str, Any]:
    """Skills your jobs ask for that you lack: which are close to what you know, and what to learn first."""
    have = _resume_skills(db, user)
    apps = [a for a in _applications(db, user) if a.job.is_active and a.status != ApplicationStatus.SKIPPED]
    demand: Counter[str] = Counter()
    for app in apps:
        demand.update(set(job_skills(app.job)))
    missing = {skill: count for skill, count in demand.items() if skill not in have}
    wanted_top = [skill for skill, _ in Counter(missing).most_common(25)]
    return {
        "have": sorted(display_skill(s) for s in have),
        "jobs_considered": len(apps),
        "in_demand": [{"skill": s, "label": display_skill(s), "jobs": c, "have": s in have}
                      for s, c in demand.most_common(15)],
        "transferable": skill_graph.transferable(have, wanted_top)[:limit],
        "learn_next": skill_graph.learn_next(have, missing, limit),
        "graph": skill_graph.graph_for(have, wanted_top[:12]),
        "algorithm": {"name": "multi-source Dijkstra + heap top-K, BFS for the neighbourhood", "time": "O((V + E) log V)"},
    }
