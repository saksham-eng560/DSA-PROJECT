"""Analytics & reporting (PLAN.md §17)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.enums import (
    INTERVIEW_STATUSES,
    RESPONSE_STATUSES,
    SUBMITTED_STATUSES,
    ApplicationStatus,
)
from app.models.interview import Interview
from app.models.user import User


def _pct(num: int, den: int) -> float:
    return round(100.0 * num / den, 1) if den else 0.0


def compute_overview(db: Session, user: User, days: int | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    query = select(Application).options(joinedload(Application.job)).where(Application.user_id == user.id)
    if days:
        query = query.where(Application.updated_at >= now - timedelta(days=days))
    apps = db.scalars(query).unique().all()

    by_status = Counter(a.status.value for a in apps)
    submitted = [a for a in apps if a.status in SUBMITTED_STATUSES or a.submitted_at]
    responded = [a for a in submitted if a.status in RESPONSE_STATUSES or a.first_response_at]
    interviewed = [a for a in submitted if a.status in INTERVIEW_STATUSES]
    offers = [a for a in apps if a.status in (ApplicationStatus.OFFER, ApplicationStatus.ACCEPTED)]
    response_days = [
        (a.first_response_at - a.submitted_at).total_seconds() / 86400
        for a in submitted if a.first_response_at and a.submitted_at and a.first_response_at >= a.submitted_at
    ]

    # Timeline (last 30 days)
    start = (now - timedelta(days=29)).date()
    timeline: dict[str, dict[str, int]] = {
        (start + timedelta(days=i)).isoformat(): {"discovered": 0, "applied": 0, "responses": 0} for i in range(30)
    }
    for a in apps:
        if a.created_at and a.created_at.date() >= start:
            timeline[a.created_at.date().isoformat()]["discovered"] += 1
        if a.submitted_at and a.submitted_at.date() >= start:
            timeline[a.submitted_at.date().isoformat()]["applied"] += 1
        if a.first_response_at and a.first_response_at.date() >= start:
            timeline[a.first_response_at.date().isoformat()]["responses"] += 1

    # Match score histogram
    buckets = [0] * 10
    for a in apps:
        if a.match_score is not None:
            buckets[min(9, max(0, a.match_score // 10))] += 1
    histogram = [{"range": f"{i * 10}-{i * 10 + 9 if i < 9 else 100}", "count": c} for i, c in enumerate(buckets)]

    # Platform effectiveness
    platform: dict[str, dict[str, int]] = defaultdict(lambda: {"discovered": 0, "applied": 0, "responses": 0, "interviews": 0})
    for a in apps:
        key = a.job.source_platform.value if a.job else "unknown"
        platform[key]["discovered"] += 1
        if a in submitted:
            platform[key]["applied"] += 1
        if a in responded:
            platform[key]["responses"] += 1
        if a in interviewed:
            platform[key]["interviews"] += 1
    platforms = [
        {"platform": k, **v, "response_rate": _pct(v["responses"], v["applied"]), "interview_rate": _pct(v["interviews"], v["applied"])}
        for k, v in sorted(platform.items(), key=lambda kv: -kv[1]["applied"])
    ]

    # Keywords correlated with callbacks (lift of skill frequency among interviewed vs all submitted)
    # A "positive outcome" is reaching an interview stage or a non-rejection response.
    interviewed_ids = {a.id for a in interviewed}
    responded_ids = {a.id for a in responded}
    positive_ids = {a.id for a in submitted
                    if a.id in interviewed_ids or (a.id in responded_ids and a.status != ApplicationStatus.REJECTED)}
    all_skills: Counter[str] = Counter()
    good_skills: Counter[str] = Counter()
    for a in submitted:
        skills = set((a.job.extracted_skills or []) if a.job else [])
        all_skills.update(skills)
        if a.id in positive_ids:
            good_skills.update(skills)
    keywords = []
    base_rate = len(positive_ids) / len(submitted) if submitted else 0
    for skill, total in all_skills.most_common(60):
        if total < 2:
            continue
        rate = good_skills[skill] / total
        keywords.append({"keyword": skill, "applications": total, "callbacks": good_skills[skill],
                         "callback_rate": round(rate * 100, 1), "lift": round(rate / base_rate, 2) if base_rate else 0})
    keywords.sort(key=lambda k: (-k["callback_rate"], -k["applications"]))

    upcoming = db.scalars(
        select(Interview).join(Application, Application.id == Interview.application_id)
        .where(Application.user_id == user.id, Interview.scheduled_at >= now).order_by(Interview.scheduled_at).limit(5)
    ).all()
    runs = db.scalars(select(AgentRun).where(AgentRun.user_id == user.id).order_by(AgentRun.started_at.desc()).limit(10)).all()

    return {
        "totals": {
            "total": len(apps),
            "discovered": by_status.get("discovered", 0),
            "matched": by_status.get("matched", 0),
            "preparing": by_status.get("preparing", 0),
            "pending_approval": by_status.get("pending_approval", 0),
            "approved": by_status.get("approved", 0),
            "applied": len(submitted),
            "responses": len(responded),
            "interviews": len(interviewed),
            "offers": len(offers),
            "rejected": by_status.get("rejected", 0),
            "skipped": by_status.get("skipped", 0),
            "failed": by_status.get("failed", 0),
        },
        "by_status": dict(by_status),
        "rates": {
            "response_rate": _pct(len(responded), len(submitted)),
            "interview_rate": _pct(len(interviewed), len(submitted)),
            "offer_rate": _pct(len(offers), len(interviewed)),
            "avg_days_to_response": round(sum(response_days) / len(response_days), 1) if response_days else None,
        },
        "timeline": [{"date": d, **v} for d, v in timeline.items()],
        "match_distribution": histogram,
        "platforms": platforms,
        "top_keywords": keywords[:12],
        "upcoming_interviews": [
            {"id": str(i.id), "company": i.application.job.company_name, "role": i.application.job.role_title,
             "scheduled_at": i.scheduled_at.isoformat(), "type": i.interview_type.value if i.interview_type else None}
            for i in upcoming
        ],
        "recent_runs": [
            {"id": str(r.id), "run_type": r.run_type, "status": r.status, "started_at": r.started_at.isoformat(),
             "jobs_discovered": r.jobs_discovered, "jobs_matched": r.jobs_matched, "errors_count": r.errors_count}
            for r in runs
        ],
    }
