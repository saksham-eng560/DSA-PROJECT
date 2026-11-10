"""DSA Lab, search, graphs and insights: the endpoints behind the data-structures features."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.api.deps import DB, CurrentUser, parse_uuid
from app.api.serializers import job_out
from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.models.job import Job
from app.services import agent_orchestrator as orch
from app.services import (
    dsa_catalog,
    dsa_lab,
    duplicates,
    email_intent,
    insights,
    pipeline_graph,
    resume_optimizer,
    search_index,
    skill_graph,
    state_machine,
)

router = APIRouter(tags=["dsa"])


# --------------------------------------------------------------------------- search
@router.get("/search/suggest")
def suggest(user: CurrentUser, db: DB, q: str = Query(default="", max_length=80),
            limit: int = Query(default=8, ge=1, le=20)) -> dict:
    """Search-as-you-type completions from the user's trie of titles, companies, skills and cities."""
    return {"query": q, "suggestions": search_index.suggest(db, user, q, limit)}


@router.get("/search")
def search(user: CurrentUser, db: DB, q: str = Query(min_length=1, max_length=200),
           limit: int = Query(default=20, ge=1, le=50)) -> dict:
    """Full-text search over the user's jobs, ranked with BM25; fixes typos and completes the last word."""
    result = search_index.search(db, user, q, limit)
    ids = [job_id for job_id, _ in result["hits"]]
    rows = db.execute(select(Job, Application).join(Application, Application.job_id == Job.id)
                      .where(Application.user_id == user.id, Job.id.in_(ids))).all() if ids else []
    by_id = {job.id: (job, app) for job, app in rows}
    prefs = user.prefs
    items = [{**job_out(*by_id[job_id], prefs), "relevance": score} for job_id, score in result["hits"] if job_id in by_id]
    return {"query": q, "items": items, "matched": result["matched"], "terms": result["terms"],
            "corrections": result["corrections"], "took_ms": result["took_ms"], "index": result["index"],
            "cache": search_index.cache_stats()}


# --------------------------------------------------------------------------- catalogue and lab
@router.get("/dsa/catalog")
def catalog(user: CurrentUser) -> dict:
    entries = dsa_catalog.catalog()
    kinds: dict[str, int] = {}
    for entry in entries:
        kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
    return {"entries": entries, "kinds": kinds, "demos": sorted(dsa_lab.DEMOS)}


class DemoIn(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/dsa/demo/{name}")
def demo(name: str, body: DemoIn, user: CurrentUser) -> dict:
    """Run one of the project's structures on a small input and return each intermediate state."""
    try:
        return {"name": name, "result": dsa_lab.run_demo(name, body.params)}
    except KeyError as exc:
        raise HTTPException(404, "Unknown demo") from exc
    except dsa_lab.DemoError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/dsa/benchmarks")
def benchmarks(user: CurrentUser) -> dict:
    """Each structure timed against the naive alternative, measured on this server."""
    return dsa_lab.benchmarks()


@router.get("/dsa/live")
def live(user: CurrentUser, db: DB) -> dict:
    """The structures holding *this user's* data right now: index sizes, cache hit rate, graph sizes."""
    index = search_index.get_index(db, user)
    graph = skill_graph.skill_graph()
    return {
        "search_index": {**index.index.stats(), "build_ms": index.build_ms, "jobs": index.job_count},
        "trie": index.trie.stats(),
        "lru_cache": search_index.cache_stats(),
        "skill_graph": {"skills": len(graph), "links": graph.edge_count(), "clusters": len(graph.connected_components())},
        "state_machine": state_machine.describe()["totals"],
        "pipeline": pipeline_graph.describe()["totals"],
        "email_classifier": email_intent.describe(),
    }


# --------------------------------------------------------------------------- graphs
@router.get("/graph/pipeline")
def pipeline(user: CurrentUser) -> dict:
    """The agent pipeline as a DAG: nodes, edges, topological order, parallel layers, critical path."""
    return pipeline_graph.describe()


@router.get("/graph/lifecycle")
def lifecycle(user: CurrentUser, db: DB, application_id: str | None = None) -> dict:
    """The application status state machine; with ``application_id``, where that application stands in it."""
    if application_id is None:
        data = state_machine.describe()
        rows = db.execute(select(Application.status, func.count()).where(Application.user_id == user.id)
                          .group_by(Application.status)).all()
        counts = {status.value: count for status, count in rows}
        for node in data["nodes"]:
            node["count"] = counts.get(node["id"], 0)
        return data
    app = db.scalar(select(Application).options(joinedload(Application.history))
                    .where(Application.id == parse_uuid(application_id), Application.user_id == user.id))
    if app is None:
        raise HTTPException(404, "Application not found")
    visited = [h.new_status for h in app.history]
    return state_machine.describe(app.status, visited)


@router.get("/graph/skills")
def skills(user: CurrentUser, db: DB) -> dict:
    """Your skills against the ones your jobs want: transferable skills, what to learn next, and the graph."""
    return insights.skill_gap(db, user)


@router.get("/graph/skills/path")
def skill_path(user: CurrentUser, db: DB, target: str = Query(min_length=1, max_length=60)) -> dict:
    """Cheapest learning path from the skills on your resume to ``target`` (multi-source Dijkstra)."""
    have = insights._resume_skills(db, user)
    path = skill_graph.learning_path(have, target)
    if path is None:
        raise HTTPException(404, "That skill isn't in the graph, or nothing you know leads to it")
    return {**path, "related": [{"skill": s, "label": skill_graph.display_skill(s)}
                                for s in skill_graph.related_skills(target)[:8]]}


@router.get("/graph/duplicates")
def duplicate_groups(user: CurrentUser, db: DB, limit: int = Query(default=30, ge=1, le=100)) -> dict:
    """The same job posted on several sites, grouped with Union-Find."""
    return duplicates.find_duplicates(db, user, limit)


# --------------------------------------------------------------------------- insights
@router.get("/insights/best-matches")
def best_matches(user: CurrentUser, db: DB, k: int = Query(default=10, ge=1, le=50)) -> dict:
    return insights.best_matches(db, user, k)


@router.get("/insights/summary")
def summary(user: CurrentUser, db: DB) -> dict:
    return {"scores": insights.score_summary(db, user), "best": insights.best_matches(db, user, 5)}


@router.get("/insights/activity")
def activity(user: CurrentUser, db: DB, days: int = Query(default=90, ge=7, le=365)) -> dict:
    return insights.activity(db, user, days)


@router.get("/insights/schedule")
def schedule(user: CurrentUser, db: DB, days: int = Query(default=7, ge=1, le=30)) -> dict:
    return insights.interview_schedule(db, user, days)


@router.get("/insights/clusters")
def clusters(user: CurrentUser, db: DB, k: int = Query(default=5, ge=2, le=8)) -> dict:
    return insights.job_clusters(db, user, k)


@router.get("/insights/reply-model")
def reply_model(user: CurrentUser, db: DB) -> dict:
    return insights.reply_model(db, user)


@router.get("/insights/similar/{job_id}")
def similar(job_id: str, user: CurrentUser, db: DB, limit: int = Query(default=5, ge=1, le=20)) -> dict:
    return {"items": insights.similar_jobs(db, user, parse_uuid(job_id), limit)}


@router.get("/insights/resume-fit/{job_id}")
def resume_fit(job_id: str, user: CurrentUser, db: DB, lines: int = Query(default=resume_optimizer.DEFAULT_LINES, ge=4, le=80)) -> dict:
    """Which of your resume's bullets to keep for this job on one page (0/1 knapsack)."""
    job = db.get(Job, parse_uuid(job_id))
    owned = job is not None and db.scalar(select(Application.id).where(
        Application.user_id == user.id, Application.job_id == job.id, Application.status != ApplicationStatus.SKIPPED)
        .limit(1)) is not None
    if job is None or not owned:
        raise HTTPException(404, "Job not found")
    master = orch.get_master_resume(db, user)
    if master is None:
        raise HTTPException(409, "Upload your resume first")
    return {"job": {"id": str(job.id), "role_title": job.role_title, "company_name": job.company_name},
            **resume_optimizer.optimize(master.parsed_content, job, lines)}
