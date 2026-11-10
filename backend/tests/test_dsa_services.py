"""The services that put the data structures to work, and their API endpoints."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.application import Application
from app.models.enums import ApplicationStatus as S
from app.models.enums import ATSPlatform, JobType
from app.models.job import Job
from app.models.user import User
from app.scrapers import ScrapedJob
from app.services import (
    dsa_catalog,
    dsa_lab,
    duplicates,
    pipeline_graph,
    resume_optimizer,
    search_index,
    skill_graph,
    state_machine,
)
from app.services.agent.jobs import SeenPostings, upsert_job
from app.services.text_utils import extract_skills, extract_skills_regex
from tests.conftest import FIXTURES, SAMPLE_RESUME_TEXT

APP_DIR = Path(__file__).resolve().parents[1] / "app"

JOBS = [
    # (company, title, location, platform, score, status, description)
    ("Zomato", "Python Backend Intern", "Gurugram, India", ATSPlatform.LINKEDIN, 88, S.MATCHED,
     "Build REST APIs with Python, FastAPI and PostgreSQL. Docker and AWS are a plus."),
    ("Zomato", "Backend Intern - Python (Summer 2027)", "Gurugram, India", ATSPlatform.INDEED, 84, S.MATCHED,
     "Python backend internship: FastAPI, PostgreSQL, Redis."),
    ("Razorpay", "Machine Learning Intern", "Bengaluru, India", ATSPlatform.GREENHOUSE, 72, S.MATCHED,
     "Train PyTorch models, deep learning, NLP and LLM evaluation. Python required."),
    ("Cred", "Frontend Intern", "Bengaluru, India", ATSPlatform.LEVER, 55, S.MATCHED,
     "React, TypeScript and Next.js dashboards with Tailwind CSS."),
    ("Swiggy", "Data Analyst Intern", "Remote", ATSPlatform.LINKEDIN, 61, S.MATCHED,
     "SQL, Excel, Tableau dashboards and statistics for the growth team."),
    ("Meesho", "SDE Intern", "Bengaluru, India", ATSPlatform.LINKEDIN, 79, S.APPLIED,
     "Java, Spring Boot, Kafka and microservices. Data structures and algorithms."),
    ("Meesho", "Software Development Engineer Intern", "Bengaluru, India", ATSPlatform.GREENHOUSE, 77, S.MATCHED,
     "Java, Spring Boot and microservices on Kubernetes."),
    ("Flipkart", "DevOps Intern", "Bengaluru, India", ATSPlatform.WORKDAY, 48, S.INTERVIEW,
     "Kubernetes, Terraform, CI/CD pipelines, Prometheus and Linux."),
]


def seed_jobs(email: str = "jane@example.com") -> list[str]:
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=email).one()
        ids = []
        now = datetime.now(UTC)
        for index, (company, title, location, platform, score, status, description) in enumerate(JOBS):
            job = Job(company_name=company, role_title=title, description=description, location=location,
                      is_remote=location == "Remote", source_url=f"https://jobs.example.com/{index}",
                      source_platform=platform, job_type=JobType.INTERNSHIP, extracted_skills=extract_skills(description),
                      posted_date=date.today() - timedelta(days=index), discovered_at=now - timedelta(hours=index),
                      deadline_date=date.today() + timedelta(days=2) if index == 3 else None)
            db.add(job)
            db.flush()
            app = Application(user_id=user.id, job_id=job.id, status=status, match_score=score,
                              submitted_at=now - timedelta(days=3) if status in (S.APPLIED, S.INTERVIEW) else None)
            db.add(app)
            db.flush()
            ids.append(str(app.id))
        db.commit()
        return ids


@pytest.fixture
def seeded(auth_client: TestClient, master_resume: dict) -> list[str]:
    search_index.invalidate()
    return seed_jobs()


# --------------------------------------------------------------------------- skill extraction (Aho-Corasick)
def test_automaton_extracts_exactly_what_the_regexes_did():
    texts = [SAMPLE_RESUME_TEXT, *(description for *_, description in JOBS)]
    for path in sorted((FIXTURES / "scrapers").iterdir()):
        texts.append(path.read_text(encoding="utf-8")[:20000])
    texts += ["Java and JavaScript, C/C++, C#, .NET; Node.js/React", "golang", "javascripts and c++11 and r, python3",
              "REST API, RESTful services, CI/CD and A/B testing", ""]
    for text in texts:
        assert extract_skills(text, ["Rust", "Zig"]) == extract_skills_regex(text, ["Rust", "Zig"])
    assert extract_skills("We use JavaScript") == ["javascript"]  # "java" is not found inside "javascript"


# --------------------------------------------------------------------------- graphs
def test_skill_graph_paths_and_transferable_skills():
    graph = skill_graph.skill_graph()
    assert len(graph) > 100 and not any(weight <= 0 for _, _, weight in graph.edges())
    assert skill_graph.transferable(["fastapi", "react"], ["Flask", "Next.js", "kubernetes"]) == [
        {"skill": "flask", "label": "Flask", "via": "fastapi", "via_label": "FastAPI", "effort": 1},
        {"skill": "next.js", "label": "Next.js", "via": "react", "via_label": "React", "effort": 1},
    ]
    assert skill_graph.transferable(["python"], ["java", "machine learning"]) == []  # related, but a real step away
    path = skill_graph.learning_path(["python", "sql"], "rag")
    assert path["path"][0] == "python" and path["path"][-1] == "rag" and path["effort"] == 3
    assert skill_graph.learning_path(["python"], "python")["effort"] == 0
    assert skill_graph.learning_path(["python"], "not a skill") is None
    ranked = skill_graph.learn_next(["python"], {"fastapi": 5, "kubernetes": 5, "python": 9})
    assert [r["skill"] for r in ranked] == ["fastapi", "kubernetes"]  # same demand, FastAPI is one step away
    assert "flask" in skill_graph.related_skills("fastapi")
    view = skill_graph.graph_for(["python"], ["rag"])
    states = {node["id"]: node["state"] for node in view["nodes"]}
    assert states["python"] == "have" and states["rag"] == "wanted" and view["totals"]["skills"] == len(graph)
    assert all(edge["source"] in states and edge["target"] in states for edge in view["edges"])


def test_state_machine_covers_every_status():
    graph = state_machine.lifecycle_graph()
    assert set(graph.nodes()) == set(S) and set(state_machine.LABELS) == set(S)
    assert {s for statuses in state_machine.STAGES.values() for s in statuses} == set(S)
    for terminal in state_machine.TERMINAL:
        assert state_machine.next_statuses(terminal) == []
    assert state_machine.steps_to(S.MATCHED) == [S.MATCHED, S.APPLIED, S.OFFER]
    assert state_machine.steps_to(S.REJECTED) is None and not state_machine.can_reach(S.WITHDRAWN, S.OFFER)
    assert all(state_machine.can_reach(S.DISCOVERED, status) for status in S)  # nothing is unreachable
    assert state_machine.is_expected(S.APPLIED, S.INTERVIEW) and not state_machine.is_expected(S.REJECTED, S.OFFER)
    described = state_machine.describe(S.PENDING_APPROVAL, [S.DISCOVERED, S.MATCHED])
    assert described["current"]["offer_reachable"] and described["current"]["visited"] == ["discovered", "matched"]
    assert {n["status"] for n in described["current"]["next"]} == {"approved", "skipped", "preparing", "applied"}


def test_pipeline_is_a_dag_and_names_real_files():
    described = pipeline_graph.describe()
    order = described["order"]
    assert all(order.index(edge["source"]) < order.index(edge["target"]) for edge in described["edges"])
    assert described["layers"][0] == ["resume", "sources"] and described["critical_path"][-1] == "copilot"
    assert {"tailor", "cover", "answers"} <= set(described["layers"][6])  # independent, so they share a layer
    for node in pipeline_graph.NODES:
        assert (APP_DIR / node.module).is_file(), node.module
    assert described["totals"]["nodes"] == len(pipeline_graph.NODES)


def test_catalog_points_at_real_code_and_is_searchable():
    for entry in dsa_catalog.CATALOG:
        assert (APP_DIR / entry.module).is_file(), entry.module
        assert (APP_DIR / entry.used_in).is_file(), entry.used_in
        assert not entry.demo or entry.demo in dsa_lab.DEMOS or entry.demo == "pipeline", entry.demo
    assert len({entry.id for entry in dsa_catalog.CATALOG}) == len(dsa_catalog.CATALOG)
    assert dsa_catalog.find("how does the trie autocomplete work")[0]["id"] == "trie"
    assert dsa_catalog.find("bloom filter")[0]["id"] == "bloom"


# --------------------------------------------------------------------------- duplicates (union-find)
def test_duplicate_postings_are_grouped():
    assert duplicates.title_key("SDE Intern (Summer 2027)") == duplicates.title_key("Software Development Engineer Internship")
    assert duplicates.same_role("python backend internship", "backend internship python")
    assert not duplicates.same_role("backend internship", "frontend internship")
    jobs = [{"id": i, "company": company, "title": title} for i, (company, title) in enumerate([
        ("Meesho", "SDE Intern"), ("Meesho Technologies", "Software Development Engineer Intern"),
        ("Meesho", "Software Development Engineer Internship - Summer 2027"), ("Meesho", "Product Design Intern"),
        ("Zomato", "SDE Intern"), ("Zomato Ltd.", "SDE Intern")])]
    groups, stats = duplicates.cluster(jobs)
    assert sorted(sorted(job["id"] for job in group) for group in groups) == [[0, 1, 2], [4, 5]]
    assert stats["duplicates"] == 3 and stats["comparisons"] < stats["all_pairs"]  # bucketing skipped cross-company pairs


def test_duplicates_endpoint(auth_client: TestClient, seeded: list[str]):
    data = auth_client.get("/api/v1/graph/duplicates").json()
    assert {(g["company"], g["size"]) for g in data["groups"]} == {("Zomato", 2), ("Meesho", 2)}
    zomato = next(g for g in data["groups"] if g["company"] == "Zomato")
    assert zomato["platforms"] == ["indeed", "linkedin"] and data["stats"]["jobs"] == len(JOBS)


# --------------------------------------------------------------------------- Bloom filter in the scan
def test_bloom_filter_skips_duplicate_queries_without_losing_jobs(db):
    def scraped(n: int, title: str = "Backend Intern") -> ScrapedJob:
        return ScrapedJob(company_name="Acme", role_title=title, description="Python role", location="Delhi",
                          source_url=f"https://acme.example/{n}", source_platform=ATSPlatform.GREENHOUSE)

    first, created = upsert_job(db, scraped(1))
    assert created
    db.commit()
    seen = SeenPostings.load(db)
    again, created = upsert_job(db, scraped(2), seen)  # same job on another URL: "maybe", so the exact query runs
    assert again.id == first.id and not created and (seen.checked, seen.skipped) == (1, 0)
    other, created = upsert_job(db, scraped(3, "Data Intern"), seen)  # certainly new: the query is skipped
    assert created and other.id != first.id and seen.skipped == 1
    twin, created = upsert_job(db, scraped(4, "Data Intern"), seen)  # added to the filter above, so still caught
    assert twin.id == other.id and not created
    assert "certainly new" in seen.summary()
    assert db.query(Job).count() == 2


# --------------------------------------------------------------------------- search (trie + inverted index + LRU)
def test_search_suggest_and_ranking(auth_client: TestClient, seeded: list[str]):
    suggestions = auth_client.get("/api/v1/search/suggest?q=back").json()["suggestions"]
    assert {"Python Backend Intern", "Backend Intern - Python (Summer 2027)"} <= {s["text"] for s in suggestions}
    kinds = {s["kind"] for s in auth_client.get("/api/v1/search/suggest?q=p").json()["suggestions"]}
    assert "title" in kinds and "skill" in kinds
    assert auth_client.get("/api/v1/search/suggest?q=zom").json()["suggestions"][0] == {
        "text": "Zomato", "kind": "company", "weight": 3.0}
    assert auth_client.get("/api/v1/search/suggest?q=").json()["suggestions"] == []

    result = auth_client.get("/api/v1/search?q=python backend").json()
    assert [item["company_name"] for item in result["items"][:2]] == ["Zomato", "Zomato"]
    assert result["items"][0]["relevance"] >= result["items"][1]["relevance"] and result["index"]["jobs"] == len(JOBS)
    typo = auth_client.get("/api/v1/search?q=kuberntes").json()
    assert typo["corrections"] == {"kuberntes": "kubernetes"} and typo["items"][0]["company_name"] in ("Flipkart", "Meesho")
    assert auth_client.get("/api/v1/search?q=zzzzqqq").json()["items"] == []
    assert auth_client.get("/api/v1/search").status_code == 422


def test_search_index_is_cached_until_jobs_change(auth_client: TestClient, seeded: list[str]):
    before = search_index.cache_stats()["misses"]
    for _ in range(3):
        auth_client.get("/api/v1/search?q=intern")
    stats = search_index.cache_stats()
    assert stats["misses"] - before == 1 and stats["hits"] >= 2  # built once, then served from the LRU cache
    with SessionLocal() as db:
        user = db.query(User).one()
        first = search_index.get_index(db, user)
        assert search_index.get_index(db, user) is first
        job = Job(company_name="Nvidia", role_title="GPU Intern", description="CUDA", source_url="https://n/1",
                  source_platform=ATSPlatform.WORKDAY)
        db.add(job)
        db.flush()
        db.add(Application(user_id=user.id, job_id=job.id))
        db.commit()
        assert search_index.get_index(db, user) is not first  # a new job changes the signature: rebuilt
    assert auth_client.get("/api/v1/search?q=nvidia").json()["items"][0]["company_name"] == "Nvidia"


def test_search_is_scoped_to_the_user(auth_client: TestClient, seeded: list[str], client: TestClient):
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/register", json={"email": "sam@example.com", "password": "supersecret1", "full_name": "Sam"})
    assert client.get("/api/v1/search?q=python").json()["items"] == []
    assert client.get("/api/v1/search/suggest?q=zom").json()["suggestions"] == []
    assert client.get("/api/v1/graph/duplicates").json()["groups"] == []


# --------------------------------------------------------------------------- insights
def test_best_matches_use_score_deadline_and_percentile(auth_client: TestClient, seeded: list[str]):
    data = auth_client.get("/api/v1/insights/best-matches?k=3").json()
    assert data["considered"] == 6  # the applied and interviewing ones are not waiting
    assert [item["match_score"] for item in data["items"]] == [88, 84, 77]
    assert data["items"][0]["percentile"] > data["items"][2]["percentile"] and data["algorithm"]["time"] == "O(n log k)"
    everything = auth_client.get("/api/v1/insights/best-matches?k=50").json()["items"]
    cred = next(item for item in everything if item["company_name"] == "Cred")
    assert cred["priority"] > cred["match_score"]  # closes in two days: urgency bonus
    summary = auth_client.get("/api/v1/insights/summary").json()["scores"]
    assert summary["count"] == 8 and summary["median"] == 74.5 and summary["max"] == 88
    assert summary["bands"]["strong (80-100)"] == 2


def test_activity_rolling_totals(auth_client: TestClient, seeded: list[str]):
    data = auth_client.get("/api/v1/insights/activity?days=30").json()
    assert data["total_applied"] == 2 and data["last_7"] == 2 and len(data["series"]) == 30
    assert data["series"][-1]["cumulative"] == 2 and data["series"][-1]["rolling_7"] == 2
    assert data["milestones"][0]["applications"] == 1 and data["best_week"]["applied"] == 2
    assert auth_client.get("/api/v1/insights/activity?days=3").status_code == 422


def test_clusters_similar_jobs_and_reply_model(auth_client: TestClient, seeded: list[str]):
    clusters = auth_client.get("/api/v1/insights/clusters?k=3").json()
    assert clusters["trained"] and sum(c["size"] for c in clusters["clusters"]) == len(JOBS)
    with SessionLocal() as db:
        zomato = db.query(Job).filter_by(role_title="Python Backend Intern").one()
        job_id = str(zomato.id)
    similar = auth_client.get(f"/api/v1/insights/similar/{job_id}").json()["items"]
    assert similar[0]["company_name"] == "Zomato" and similar[0]["similarity"] > 0.3
    model = auth_client.get("/api/v1/insights/reply-model").json()
    assert model["trained"] is False and "settled applications" in model["reason"]  # too little history: says so


def test_reply_model_trains_on_history(auth_client: TestClient, master_resume: dict):
    with SessionLocal() as db:
        user = db.query(User).one()
        old = datetime.now(UTC) - timedelta(days=30)
        for index in range(24):
            good = index % 2 == 0
            job = Job(company_name=f"Co{index}", role_title="Intern", description="Python FastAPI" if good else "Java Spring",
                      source_url=f"https://h/{index}", source_platform=ATSPlatform.GREENHOUSE if good else ATSPlatform.LINKEDIN,
                      extracted_skills=["python", "fastapi"] if good else ["java", "spring boot"])
            db.add(job)
            db.flush()
            db.add(Application(user_id=user.id, job_id=job.id, match_score=85 if good else 35, submitted_at=old,
                               status=S.INTERVIEW if good else S.APPLIED))
        for index, score in enumerate([90, 30]):
            job = Job(company_name=f"New{index}", role_title="Intern", description="Python FastAPI" if score > 50 else "Java",
                      source_url=f"https://w/{index}", source_platform=ATSPlatform.GREENHOUSE,
                      extracted_skills=["python"] if score > 50 else ["java"])
            db.add(job)
            db.flush()
            db.add(Application(user_id=user.id, job_id=job.id, match_score=score, status=S.MATCHED))
        db.commit()
    model = auth_client.get("/api/v1/insights/reply-model").json()
    assert model["trained"] and model["examples"] == 24 and model["positives"] == 12 and model["accuracy"] == 1.0
    assert model["loss"]["end"] < model["loss"]["start"]
    chances = {p["company_name"]: p["reply_chance"] for p in model["predictions"]}
    assert chances["New0"] > 0.8 > 0.2 > chances["New1"]
    assert model["predictions"][0]["why"][0]["contribution"] > 0


def test_skill_gap_and_learning_path_endpoints(auth_client: TestClient, seeded: list[str]):
    gap = auth_client.get("/api/v1/graph/skills").json()
    assert "Python" in gap["have"] and gap["jobs_considered"] == len(JOBS)
    close = {t["label"]: t["via_label"] for t in gap["transferable"]}
    assert close.get("Next.js") == "React" and "Java" not in close  # one cheap edge from what the resume has
    assert gap["learn_next"] and all(item["label"] not in gap["have"] for item in gap["learn_next"])
    assert {n["state"] for n in gap["graph"]["nodes"]} >= {"have", "wanted"}
    path = auth_client.get("/api/v1/graph/skills/path?target=RAG").json()
    assert path["labels"][-1] == "RAG" and path["effort"] > 0 and path["related"]
    assert auth_client.get("/api/v1/graph/skills/path?target=basket weaving").status_code == 404


def test_lifecycle_and_pipeline_endpoints(auth_client: TestClient, seeded: list[str]):
    overview = auth_client.get("/api/v1/graph/lifecycle").json()
    counts = {node["id"]: node["count"] for node in overview["nodes"]}
    assert counts["matched"] == 6 and counts["applied"] == 1 and counts["interview"] == 1 and len(counts) == 17
    one = auth_client.get(f"/api/v1/graph/lifecycle?application_id={seeded[5]}").json()["current"]
    assert one["status"] == "applied" and one["offer_reachable"] and one["steps_to_offer"] == ["applied", "offer"]
    assert auth_client.get("/api/v1/graph/lifecycle?application_id=00000000-0000-0000-0000-000000000000").status_code == 404
    pipeline = auth_client.get("/api/v1/graph/pipeline").json()
    assert pipeline["totals"]["nodes"] == 18 and pipeline["order"][0] in ("resume", "sources")


# --------------------------------------------------------------------------- interviews (interval sweep)
def test_interview_clashes_are_reported(auth_client: TestClient, seeded: list[str]):
    start = (datetime.now(UTC) + timedelta(days=2)).replace(hour=6, minute=0, second=0, microsecond=0)
    first = auth_client.post("/api/v1/interviews", json={"application_id": seeded[5], "scheduled_at": start.isoformat(),
                                                         "duration_minutes": 60})
    assert first.status_code == 201 and first.json()["conflicts"] == []
    second = auth_client.post("/api/v1/interviews", json={
        "application_id": seeded[7], "scheduled_at": (start + timedelta(minutes=30)).isoformat(), "duration_minutes": 60})
    assert [c["company_name"] for c in second.json()["conflicts"]] == ["Meesho"]
    schedule = auth_client.get("/api/v1/insights/schedule?days=7").json()
    assert schedule["most_at_once"] == 2 and schedule["attendable"] == 1 and len(schedule["interviews"]) == 2
    assert schedule["conflicts"][0]["overlap_minutes"] == 30
    assert all(slot["minutes"] >= 60 for slot in schedule["free_slots"])
    moved = auth_client.patch(f"/api/v1/interviews/{second.json()['id']}",
                              json={"scheduled_at": (start + timedelta(hours=1)).isoformat()})
    assert moved.json()["conflicts"] == []  # back to back is not a clash


# --------------------------------------------------------------------------- undo stack
def test_undo_last_pops_the_most_recent_swipe(auth_client: TestClient, seeded: list[str], monkeypatch):
    monkeypatch.setattr("app.worker.dispatch.enqueue", lambda *a, **k: None)
    assert auth_client.post("/api/v1/review/undo-last").status_code == 409  # empty stack
    auth_client.post(f"/api/v1/review/{seeded[3]}", json={"decision": "skip"})
    auth_client.post(f"/api/v1/review/{seeded[4]}", json={"decision": "skip"})
    history = auth_client.get("/api/v1/review/history").json()
    assert history["depth"] == 2 and history["items"][0]["company_name"] == "Swiggy"  # newest on top
    undone = auth_client.post("/api/v1/review/undo-last").json()
    assert undone["card"]["job"]["company_name"] == "Swiggy" and undone["remaining"] == 1
    assert auth_client.post("/api/v1/review/undo-last").json()["card"]["job"]["company_name"] == "Cred"
    assert auth_client.post("/api/v1/review/undo-last").status_code == 409


# --------------------------------------------------------------------------- resume optimiser (knapsack)
def test_resume_fit_picks_the_most_relevant_bullets(auth_client: TestClient, seeded: list[str], master_resume: dict):
    with SessionLocal() as db:
        job = db.query(Job).filter_by(role_title="Python Backend Intern").one()
        job_id = str(job.id)
        result = resume_optimizer.optimize(master_resume["parsed_content"], job, max_lines=3)
    assert result["lines_used"] <= 3 and result["kept"] + result["dropped"] == len(result["bullets"])
    assert result["value"] >= result["greedy"]["value"]  # dynamic programming is never worse than greedy
    kept = [b["text"] for b in result["bullets"] if b["keep"]]
    assert any("FastAPI" in text for text in kept)
    assert [d["text"] for d in result["diff"] if d["op"] != "-"] == kept
    api = auth_client.get(f"/api/v1/insights/resume-fit/{job_id}?lines=40").json()
    assert api["fits_already"] and api["dropped"] == 0 and "FastAPI" in api["skills_covered"]
    assert auth_client.get("/api/v1/insights/resume-fit/00000000-0000-0000-0000-000000000000").status_code == 404


# --------------------------------------------------------------------------- DSA Lab
def test_every_demo_runs_and_rejects_bad_input(auth_client: TestClient):
    catalog = auth_client.get("/api/v1/dsa/catalog").json()
    assert len(catalog["entries"]) == len(dsa_catalog.CATALOG) and set(catalog["demos"]) == set(dsa_lab.DEMOS)
    for name in dsa_lab.DEMOS:
        response = auth_client.post(f"/api/v1/dsa/demo/{name}", json={})
        assert response.status_code == 200, (name, response.text)
    heap = auth_client.post("/api/v1/dsa/demo/heap", json={"params": {"scores": [5, 1, 9, 3, 7], "k": 2}}).json()["result"]
    assert heap["result"] == [9, 7] and heap["steps"][-1]["heap"] == [7, 9]
    edit = auth_client.post("/api/v1/dsa/demo/edit_distance", json={"params": {"a": "kitten", "b": "sitting"}}).json()["result"]
    assert edit["distance"] == 3 and edit["table"][-1][-1] == 3
    knap = auth_client.post("/api/v1/dsa/demo/knapsack", json={"params": {"weights": [1, 2, 3], "values": [6, 10, 12],
                                                                             "capacity": 5}}).json()["result"]
    assert knap["best"] == 22 and knap["greedy"]["value"] == 16 and knap["chosen"] == [1, 2]
    bloom = auth_client.post("/api/v1/dsa/demo/bloom", json={}).json()["result"]
    assert all(p["maybe_present"] for p in bloom["probes"] if p["actually_present"])  # never a false negative
    graph = auth_client.post("/api/v1/dsa/demo/graph", json={"params": {"have": ["python"], "target": "kubernetes"}}).json()["result"]
    assert graph["path"][0] == "python" and graph["path"][-1] == "kubernetes"
    assert auth_client.post("/api/v1/dsa/demo/nope", json={}).status_code == 404
    assert auth_client.post("/api/v1/dsa/demo/knapsack", json={"params": {"weights": [1], "values": [1, 2]}}).status_code == 422
    assert auth_client.post("/api/v1/dsa/demo/heap", json={"params": {"scores": ["x"]}}).status_code == 422
    assert auth_client.post("/api/v1/dsa/demo/graph", json={"params": {"target": "nonsense"}}).status_code == 422


def test_live_structures_and_auth(auth_client: TestClient, seeded: list[str], client: TestClient):
    live = auth_client.get("/api/v1/dsa/live").json()
    assert live["search_index"]["documents"] == len(JOBS) and live["trie"]["words"] > 20
    assert live["skill_graph"]["skills"] > 100 and live["state_machine"]["states"] == 17
    assert live["email_classifier"]["examples"] > 100
    client.post("/api/v1/auth/logout")
    for path in ("/dsa/catalog", "/dsa/live", "/search?q=x", "/graph/pipeline", "/insights/summary"):
        assert client.get(f"/api/v1{path}").status_code == 401, path


def test_benchmarks_report_measured_numbers():
    rows = {row["structure"]: row for row in dsa_lab.benchmarks()["rows"]}
    assert {"Trie", "Binary heap (top-K)", "Aho-Corasick automaton", "Bloom filter", "Inverted index + BM25",
            "Fenwick tree", "Union-Find"} <= set(rows)
    assert all(row["ours_ms"] > 0 and row["naive_ms"] > 0 for row in rows.values())
    assert "0 false negatives" in rows["Bloom filter"]["detail"]
    assert dsa_lab.benchmarks() is dsa_lab.benchmarks()  # cached
