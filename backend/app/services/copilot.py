"""Career copilot: a chatbot that looks things up in your own data before it answers.

This is retrieval-augmented generation (RAG) in four steps, each one visible in the reply's
``trace`` so the dashboard can show how an answer was produced:

1. **Understand** - an Aho-Corasick automaton finds intent keywords in the message in one pass
   ("how many", "interview", "learn", "why", ...) and the intent with the most weight wins.
2. **Retrieve** - the intent picks a tool that reads the database: BM25 search over your jobs,
   the top-K heap, the skill graph, the interview sweep, the status state machine, or the
   catalogue of data structures. The result is a small dictionary of *facts*.
3. **Remember** - the last few messages of the conversation are loaded into a ring buffer
   (fixed size, oldest dropped first) and sent along as context.
4. **Answer** - the configured LLM (Claude, or a local Ollama model) writes the reply from the
   facts, with a JSON schema for the output. With no LLM configured, a rule-based writer turns
   the same facts into text, so the chatbot works offline too - it just sounds plainer.

The model never sees the database, only the facts retrieved for this question, and it is told
that those facts are data, not instructions (job descriptions are written by strangers).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import Counter
from collections.abc import Callable
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, joinedload

from app.dsa.linear import RingBuffer
from app.dsa.strings import AhoCorasick
from app.models.application import Application
from app.models.chat import ChatMessage, ChatSession
from app.models.enums import INTERVIEW_STATUSES, RESPONSE_STATUSES, SUBMITTED_STATUSES, ApplicationStatus
from app.models.job import Job
from app.models.user import User
from app.services import dsa_catalog, insights, llm_schemas, pipeline_graph, search_index, skill_graph, state_machine
from app.services.llm import LLMError, get_llm, render_prompt
from app.services.text_utils import canonical_skill, display_skill, extract_skills, normalize_company, normalize_text

logger = logging.getLogger(__name__)

MEMORY_MESSAGES = 8
MAX_MESSAGE_CHARS = 2000
ANSWER_SCHEMA = llm_schemas.obj(answer=llm_schemas.STR, follow_ups=llm_schemas.STR_LIST)

# intent -> (keyword, weight). Longer, more specific phrases carry more weight.
INTENT_KEYWORDS: dict[str, dict[str, float]] = {
    "stats": {"how many": 2, "stats": 2, "statistics": 2, "progress": 2, "response rate": 3, "summary": 1.5,
              "overview": 2, "how am i doing": 3, "numbers": 1.5, "so far": 1, "total": 1, "applied to": 1.5,
              "this week": 1.5, "success rate": 3},
    "recommend": {"recommend": 3, "best match": 3, "best jobs": 3, "top jobs": 3, "should i apply": 3, "top match": 3,
                  "which jobs": 2, "what should i apply": 3, "suggest": 2, "best internship": 3, "good fit": 2,
                  "top 5": 2, "top five": 2, "worth applying": 3},
    "search": {"find": 2, "search": 2, "show me": 1.5, "looking for": 2, "any jobs": 2, "jobs in": 2, "roles in": 2,
               "internships in": 2, "openings": 2, "remote": 1, "list": 1},
    "skills": {"skill": 2, "learn": 2.5, "missing": 2, "gap": 2.5, "improve": 1.5, "upskill": 3, "roadmap": 2.5,
               "what should i study": 3, "learning path": 3, "how do i become": 3, "weak": 1.5, "transferable": 3},
    "status": {"status": 2.5, "any update": 3, "heard back": 3, "hear back": 3, "what happened": 2.5, "where is my": 2.5,
               "did i apply": 3, "my application": 2.5, "next step": 2, "stuck": 1.5, "needs approval": 3,
               "pending": 2},
    "interviews": {"interview": 3, "schedule": 2, "calendar": 2.5, "clash": 3, "conflict": 3, "free slot": 3,
                   "when is my": 2, "upcoming": 1.5, "availability": 2},
    "explain": {"how does": 2.5, "how do you": 2.5, "explain": 3, "what is a": 2, "what is the": 1, "why is": 1.5,
                "algorithm": 3, "data structure": 3, "complexity": 3, "big o": 3, "trie": 3, "heap": 3, "bloom": 3,
                "graph": 2, "bfs": 3, "dfs": 3, "dijkstra": 3, "bm25": 3, "lru": 3, "cache": 2, "union find": 3,
                "knapsack": 3, "pipeline": 2.5, "how it works": 3, "how is the score": 3, "match score": 2,
                "under the hood": 3, "naive bayes": 3, "machine learning": 1.5, "database": 2.5, "hash": 2.5,
                "fenwick": 3, "binary search": 3, "sorting": 2.5, "aho": 3, "k-means": 3, "logistic": 3},
    "resume": {"resume": 3, "cv": 2.5, "bullet": 2.5, "cover letter": 2.5, "tailor": 2.5, "one page": 3, "ats": 2.5},
    "help": {"help": 2.5, "what can you do": 4, "who are you": 3, "hello": 1, "hi": 0.5, "hey": 0.5, "thanks": 1,
             "thank you": 1, "commands": 2},
}
_WORD_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
_STATUS_LABELS = {status.value: label for status, label in state_machine.LABELS.items()}
SUGGESTIONS = {
    "stats": ["Which jobs should I apply to first?", "What skills am I missing?", "Any interview clashes?"],
    "recommend": ["Why is the top one a good fit?", "What skills am I missing?", "How am I doing so far?"],
    "search": ["Show my best matches", "Find remote Python internships", "What should I learn next?"],
    "skills": ["How do I learn RAG?", "Show my best matches", "How does the skill graph work?"],
    "status": ["How am I doing so far?", "Any interviews coming up?", "Show my best matches"],
    "interviews": ["How am I doing so far?", "Show my best matches", "What skills am I missing?"],
    "explain": ["How does the trie work here?", "How is the match score calculated?", "Explain the pipeline"],
    "resume": ["What skills am I missing?", "Show my best matches", "How does the knapsack optimiser work?"],
    "help": ["How am I doing so far?", "Which jobs should I apply to first?", "What should I learn next?"],
}


def _automaton() -> tuple[AhoCorasick, dict[str, list[tuple[str, float]]]]:
    owners: dict[str, list[tuple[str, float]]] = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        for keyword, weight in keywords.items():
            owners.setdefault(keyword, []).append((intent, weight))
    return AhoCorasick(owners), owners


_INTENTS, _OWNERS = _automaton()


def _whole_word(text: str, start: int, end: int) -> bool:
    return (start == 0 or text[start - 1] not in _WORD_CHARS) and (end == len(text) or text[end] not in _WORD_CHARS)


def detect_intent(message: str) -> dict[str, Any]:
    """Score every intent by the keywords found in ``message`` (one Aho-Corasick pass)."""
    text = normalize_text(message)
    scores: Counter[str] = Counter()
    matched: list[str] = []
    seen: set[tuple[int, str]] = set()
    for start, end, keyword in _INTENTS.finditer(text):
        # A trailing "s" still counts ("interviews", "skills"); "this" must not match "hi".
        plural = end < len(text) and text[end] == "s" and _whole_word(text, start, end + 1)
        if not (_whole_word(text, start, end) or plural) or (start, keyword) in seen:
            continue
        seen.add((start, keyword))
        matched.append(keyword)
        for intent, weight in _OWNERS[keyword]:
            scores[intent] += weight
    if not scores:
        return {"intent": "search" if len(text.split()) >= 2 else "help", "confidence": 0.3, "keywords": [], "scores": {}}
    ranked = scores.most_common()
    best, top = ranked[0]
    return {"intent": best, "confidence": round(top / sum(scores.values()), 2), "keywords": matched,
            "scores": dict(ranked)}


# --------------------------------------------------------------------------- tools (retrieval)
def _cards(db: Session, user: User, job_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    if not job_ids:
        return []
    rows = db.scalars(select(Application).options(joinedload(Application.job))
                      .where(Application.user_id == user.id, Application.job_id.in_(job_ids))).unique().all()
    by_job = {app.job_id: app for app in rows}
    return [insights._job_card(by_job[job_id]) for job_id in job_ids if job_id in by_job]


def tool_stats(db: Session, user: User, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counts = dict(db.execute(select(Application.status, func.count()).where(Application.user_id == user.id)
                             .group_by(Application.status)).all())
    by_status = {status.value: count for status, count in counts.items()}
    submitted = sum(count for status, count in counts.items() if status in SUBMITTED_STATUSES)
    responded = sum(count for status, count in counts.items() if status in RESPONSE_STATUSES)
    interviewing = sum(count for status, count in counts.items() if status in INTERVIEW_STATUSES)
    recent = insights.activity(db, user, days=30)
    return {
        "total_jobs_found": sum(counts.values()),
        "waiting_in_swipe_review": by_status.get("matched", 0) + by_status.get("discovered", 0),
        "needs_your_approval": by_status.get("pending_approval", 0),
        "applied": submitted, "replies": responded, "interview_stage": interviewing,
        "offers": by_status.get("offer", 0) + by_status.get("accepted", 0), "rejected": by_status.get("rejected", 0),
        "response_rate_percent": round(100 * responded / submitted, 1) if submitted else 0.0,
        "applied_last_7_days": recent["last_7"], "applied_last_30_days": recent["last_30"],
        "by_status": {_STATUS_LABELS.get(key, key): value for key, value in by_status.items()},
    }, []


def tool_recommend(db: Session, user: User, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    best = insights.best_matches(db, user, k=5)
    facts = {"waiting_jobs_considered": best["considered"],
             "best_matches": [{"role": item["role_title"], "company": item["company_name"], "score": item["match_score"],
                               "location": item["location"], "beats_percent_of_deck": item["percentile"],
                               "deadline": item["deadline_date"]} for item in best["items"]],
             "how": "top-K selection with a binary heap, ranked by match score plus deadline urgency"}
    return facts, best["items"]


def tool_search(db: Session, user: User, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = search_index.search(db, user, message, limit=6)
    cards = _cards(db, user, [job_id for job_id, _ in result["hits"]])
    facts = {"query_terms": result["terms"], "spelling_corrections": result["corrections"],
             "jobs_matching": result["matched"], "jobs_indexed": result["index"]["jobs"],
             "results": [{"role": c["role_title"], "company": c["company_name"], "location": c["location"],
                          "score": c["match_score"], "status": _STATUS_LABELS.get(c["status"], c["status"])} for c in cards],
             "how": "BM25 ranking over an inverted index of your jobs"}
    return facts, cards


def tool_skills(db: Session, user: User, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    gap = insights.skill_gap(db, user, limit=6)
    have = [canonical_skill(s) for s in gap["have"]]
    # A skill named in the question that isn't on the resume ("how do I learn RAG?"): same automaton as the matcher.
    asked = next((skill for skill in extract_skills(message) if skill not in have and skill not in ("skill", "skills")), None)
    facts: dict[str, Any] = {
        "skills_on_your_resume": gap["have"][:25],
        "most_requested_by_your_jobs": [{"skill": d["label"], "jobs": d["jobs"], "you_have_it": d["have"]}
                                        for d in gap["in_demand"][:8]],
        "learn_next": [{"skill": item["label"], "jobs_asking": item["jobs"], "path": item["path"]} for item in gap["learn_next"]],
        "close_to_what_you_know": [{"skill": t["label"], "because_you_know": t["via_label"]} for t in gap["transferable"]],
        "how": "skill graph: direct neighbours for close skills, Dijkstra for the cheapest learning path",
    }
    if asked:
        path = skill_graph.learning_path(have, asked)
        facts["asked_about"] = {"skill": display_skill(asked),
                                "learning_path": path["labels"] if path else None,
                                "effort": path["effort"] if path else None}
    return facts, []


def tool_status(db: Session, user: User, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = db.execute(select(Job.company_name, Application)
                      .join(Application, and_(Application.job_id == Job.id, Application.user_id == user.id))
                      .where(Application.status.not_in([ApplicationStatus.SKIPPED]))
                      .order_by(Application.updated_at.desc()).limit(1500)).all()
    by_company: dict[str, list[Application]] = {}
    for company, app in rows:
        key = normalize_company(company)
        if len(key) >= 2:
            by_company.setdefault(key, []).append(app)
    text = normalize_company(message)
    mentioned = {company for start, end, company in AhoCorasick(by_company).finditer(text) if _whole_word(text, start, end)}
    if mentioned:
        apps = [app for company in mentioned for app in by_company[company]][:6]
    else:  # no company named: the applications that are in motion
        active = (ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.PREPARING, ApplicationStatus.APPROVED,
                  *RESPONSE_STATUSES, ApplicationStatus.APPLIED)
        apps = [app for _, app in rows if app.status in active][:6]
    items = []
    for app in apps:
        path = state_machine.steps_to(app.status)
        items.append({"role": app.job.role_title, "company": app.job.company_name,
                      "status": _STATUS_LABELS.get(app.status.value, app.status.value),
                      "updated": app.updated_at.date().isoformat() if app.updated_at else None,
                      "what_can_happen_next": [state_machine.LABELS[s] for s in state_machine.next_statuses(app.status)][:5],
                      "steps_to_offer": len(path) - 1 if path else None,
                      "note": app.manual_review_reason if app.needs_manual_review else None})
    facts = {"companies_recognised": sorted(mentioned), "applications": items,
             "how": "Aho-Corasick finds your companies in the message; the status state machine (a directed graph) gives the next steps"}
    return facts, [insights._job_card(app) for app in apps]


def tool_interviews(db: Session, user: User, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    schedule = insights.interview_schedule(db, user, days=14)
    return {"upcoming": schedule["interviews"][:6], "clashes": schedule["conflicts"],
            "most_at_the_same_time": schedule["most_at_once"], "free_slots": schedule["free_slots"][:5],
            "timezone": schedule["timezone"], "how": "interval sweep after sorting by start time"}, []


def tool_explain(db: Session, user: User, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entries = dsa_catalog.find(message, limit=3)
    text = normalize_text(message)
    pipeline = pipeline_graph.describe()
    facts: dict[str, Any] = {"topics": [{"name": e["name"], "idea": e["idea"], "complexity": e["complexity"],
                                         "used_for": e["feature"], "code": f"backend/app/{e['module']}"} for e in entries]}
    if any(word in text for word in ("pipeline", "how it works", "under the hood", "agent", "system")):
        facts["pipeline"] = {"stages_in_order": [n["label"] for n in sorted(pipeline["nodes"], key=lambda n: n["order"])],
                             "parallel_layers": len(pipeline["layers"]), "critical_path": pipeline["critical_path"]}
    if "score" in text or "match" in text:
        facts["match_score"] = ("0-100 in five parts of 20: skills, experience, industry, location, compensation. The LLM "
                                "scores the most promising jobs; a rule-based scorer handles the rest. A missing skill "
                                "that is joined to one you have in the skill graph is marked as close.")
    if "database" in text or "sql" in text:
        facts["database"] = ("PostgreSQL in production (SQLite locally): users, resumes, jobs, applications, status "
                             "history, communications, interviews, notifications, agent runs and chat tables. B-tree "
                             "indexes on the columns queries filter by, pgvector for embeddings, Redis for the task "
                             "queue, rate limits and live updates.")
    return facts, []


def tool_resume(db: Session, user: User, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    gap = insights.skill_gap(db, user, limit=5)
    return {"has_resume": bool(gap["have"]), "skills_on_your_resume": gap["have"][:30],
            "skills_your_jobs_want_most": [d["label"] for d in gap["in_demand"][:8]],
            "worth_adding_if_true": [t["label"] for t in gap["transferable"][:5]],
            "tools_in_the_app": ["Resume Lab: upload and edit the parsed resume",
                                 "One-page optimiser: keeps the most relevant bullets for a job (0/1 knapsack)",
                                 "Tailoring modes: original file, light reorder, or guarded AI rewrite"]}, []


def tool_help(db: Session, user: User, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return {"can_answer": ["How your search is going (applied, replies, interviews)", "Which waiting jobs to apply to first",
                           "Finding jobs by keyword, company, skill or city", "Which skills you are missing and how to learn them",
                           "The status of an application and what happens next", "Interview clashes and free slots",
                           "How any part of the system works (data structures, algorithms, pipeline, database)"]}, []


TOOLS: dict[str, Callable[[Session, User, str], tuple[dict[str, Any], list[dict[str, Any]]]]] = {
    "stats": tool_stats, "recommend": tool_recommend, "search": tool_search, "skills": tool_skills,
    "status": tool_status, "interviews": tool_interviews, "explain": tool_explain, "resume": tool_resume,
    "help": tool_help,
}


# --------------------------------------------------------------------------- answers without an LLM
def _lines(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def rule_based_answer(intent: str, facts: dict[str, Any]) -> str:
    """Turn the retrieved facts into text. Used when no LLM is configured or the LLM call fails."""
    if intent == "stats":
        if not facts["total_jobs_found"]:
            return "Nothing has been found yet. Upload your resume, then press \"Scan for jobs now\" on the Overview page."
        return (f"You have {facts['total_jobs_found']} jobs in total: {facts['waiting_in_swipe_review']} waiting in Swipe Review "
                f"and {facts['needs_your_approval']} needing your approval.\n"
                f"Applied: {facts['applied']} ({facts['applied_last_7_days']} in the last 7 days). "
                f"Replies: {facts['replies']} ({facts['response_rate_percent']}% response rate). "
                f"Interview stage: {facts['interview_stage']}. Offers: {facts['offers']}. Rejected: {facts['rejected']}.")
    if intent == "recommend":
        if not facts["best_matches"]:
            return "There are no jobs waiting in Swipe Review. Run a scan to find new ones."
        rows = [f"{m['role']} at {m['company']}: score {m['score'] if m['score'] is not None else 'n/a'}"
                + (f", beats {m['beats_percent_of_deck']}% of your deck" if m["beats_percent_of_deck"] is not None else "")
                + (f", closes {m['deadline']}" if m["deadline"] else "") for m in facts["best_matches"]]
        return f"Your best matches out of {facts['waiting_jobs_considered']} waiting jobs:\n{_lines(rows)}"
    if intent == "search":
        if not facts["results"]:
            if not facts["jobs_indexed"]:
                return "You have no jobs yet. Run a scan first, then I can search them."
            return "No job of yours matches that. Try a role, a company, a skill or a city."
        fixed = "".join(f" (read \"{wrong}\" as \"{right}\")" for wrong, right in facts["spelling_corrections"].items())
        rows = [f"{r['role']} at {r['company']}" + (f" ({r['location']})" if r["location"] else "")
                + (f": score {r['score']}" if r["score"] is not None else "") + f" [{r['status']}]" for r in facts["results"]]
        return f"{facts['jobs_matching']} of your jobs match{fixed}. The closest:\n{_lines(rows)}"
    if intent == "skills":
        parts = []
        asked = facts.get("asked_about")
        if asked:
            if asked["learning_path"]:
                parts.append(f"Cheapest path to {asked['skill']} from what you know: {' -> '.join(asked['learning_path'])}.")
            else:
                parts.append(f"{asked['skill']} isn't linked to anything on your resume yet, so it would be a fresh start.")
        if facts["learn_next"]:
            rows = [f"{item['skill']}: asked for by {item['jobs_asking']} job(s); path {' -> '.join(item['path'])}"
                    for item in facts["learn_next"][:5]]
            parts.append(f"Worth learning next (demand divided by effort):\n{_lines(rows)}")
        if facts["close_to_what_you_know"]:
            rows = [f"{t['skill']} (you know {t['because_you_know']})" for t in facts["close_to_what_you_know"][:5]]
            parts.append(f"Close to what you already know:\n{_lines(rows)}")
        if not parts:
            return ("I can't see a skill gap yet. Upload your resume and run a scan, then I can compare what you know "
                    "with what your jobs ask for.")
        return "\n".join(parts)
    if intent == "status":
        if not facts["applications"]:
            return "I couldn't find an application for that. Nothing is in progress right now either."
        rows = [f"{a['role']} at {a['company']}: {a['status']}"
                + (f" (updated {a['updated']})" if a["updated"] else "")
                + (f"; next: {', '.join(a['what_can_happen_next'][:3])}" if a["what_can_happen_next"] else "")
                + (f"; note: {a['note']}" if a["note"] else "") for a in facts["applications"]]
        lead = "Here is where those applications stand:" if facts["companies_recognised"] else "Applications in motion:"
        return f"{lead}\n{_lines(rows)}"
    if intent == "interviews":
        if not facts["upcoming"]:
            return "You have no interviews coming up. When an invite arrives by e-mail it is added automatically."
        rows = [f"{i['company_name']}, {i['role_title']}: {i['scheduled_at'][:16].replace('T', ' ')} UTC "
                f"({i['duration_minutes']} min)" for i in facts["upcoming"]]
        text = f"Upcoming interviews:\n{_lines(rows)}"
        if facts["clashes"]:
            clashes = [f"{c['first']['company_name']} and {c['second']['company_name']} overlap by {c['overlap_minutes']} min"
                       for c in facts["clashes"]]
            text += f"\nClashes:\n{_lines(clashes)}"
        else:
            text += "\nNo clashes."
        return text
    if intent == "explain":
        parts = [f"{t['name']}: {t['idea']} Used for: {t['used_for']}. "
                 f"Cost: {', '.join(f'{op} {cost}' for op, cost in t['complexity'].items())}. Code: {t['code']}."
                 for t in facts["topics"][:2]]
        if "pipeline" in facts:
            parts.append("Pipeline, in topological order: " + " -> ".join(facts["pipeline"]["stages_in_order"]) + ".")
        for key in ("match_score", "database"):
            if key in facts:
                parts.append(facts[key])
        return "\n".join(parts) or "Ask me about a specific part: the trie, the heap, the skill graph, the pipeline or the database."
    if intent == "resume":
        if not facts["has_resume"]:
            return "You haven't uploaded a resume yet. Open Resume Lab and upload a PDF or DOCX; everything else builds on it."
        text = (f"Your resume lists {len(facts['skills_on_your_resume'])} skills. Your jobs ask most for: "
                f"{', '.join(facts['skills_your_jobs_want_most']) or 'nothing yet (run a scan)'}.")
        if facts["worth_adding_if_true"]:
            text += f"\nIf you genuinely have them, make these explicit: {', '.join(facts['worth_adding_if_true'])}."
        return text + "\n" + _lines(facts["tools_in_the_app"])
    return "I'm your career copilot. I can answer from your own data:\n" + _lines(facts.get("can_answer", []))


# --------------------------------------------------------------------------- conversation
def _history(db: Session, session: ChatSession) -> RingBuffer[ChatMessage]:
    recent = db.scalars(select(ChatMessage).where(ChatMessage.session_id == session.id)
                        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(MEMORY_MESSAGES)).all()
    return RingBuffer(MEMORY_MESSAGES, reversed(recent))


def get_session(db: Session, user: User, session_id: uuid.UUID | None, first_message: str) -> ChatSession:
    if session_id is not None:
        session = db.get(ChatSession, session_id)
        if session is not None and session.user_id == user.id:
            return session
    title = " ".join(first_message.split())[:60] or "New conversation"
    session = ChatSession(user_id=user.id, title=title)
    db.add(session)
    db.flush()
    return session


def reply(db: Session, user: User, message: str, session_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Answer ``message`` and store both sides of the exchange. Returns the session and both messages."""
    started = time.perf_counter()
    message = message.strip()[:MAX_MESSAGE_CHARS]
    session = get_session(db, user, session_id, message)
    memory = _history(db, session)

    detected = detect_intent(message)
    intent = detected["intent"]
    facts, cards = TOOLS[intent](db, user, message)
    retrieved_ms = round((time.perf_counter() - started) * 1000, 1)

    answer, follow_ups, model = None, SUGGESTIONS[intent], "rules"
    llm = get_llm()
    if llm.available and intent != "help":
        history = "\n".join(f"{m.role}: {m.content[:400]}" for m in memory) or "(this is the first message)"
        prompt = render_prompt("copilot", intent=intent, history=history, facts_json=facts, question=message)
        try:
            result, provider = llm.complete_json_traced(prompt, ANSWER_SCHEMA, effort="low", max_tokens=1200, task="copilot")
            text = str(result.get("answer") or "").strip()
            if text:
                answer, model = text, provider
                asked = [str(q).strip()[:80] for q in (result.get("follow_ups") or []) if str(q).strip()]
                follow_ups = asked[:3] or follow_ups
        except LLMError as exc:
            logger.warning("Copilot LLM call failed, using the rule-based answer: %s", exc)
    if answer is None:
        answer = rule_based_answer(intent, facts)

    trace = {
        "intent": intent, "confidence": detected["confidence"], "keywords": detected["keywords"][:8],
        "tool": TOOLS[intent].__name__, "how": facts.get("how"),
        "memory_messages": len(memory), "memory_capacity": MEMORY_MESSAGES,
        "model": model, "retrieved_ms": retrieved_ms, "took_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    user_message = ChatMessage(session_id=session.id, role="user", content=message)
    db.add(user_message)
    db.flush()
    bot_message = ChatMessage(session_id=session.id, role="assistant", content=answer,
                              meta={"trace": trace, "cards": cards[:6], "follow_ups": follow_ups})
    db.add(bot_message)
    session.updated_at = bot_message.created_at or session.updated_at
    db.flush()
    return {"session": session, "user_message": user_message, "assistant_message": bot_message}
