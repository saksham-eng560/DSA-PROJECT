"""The career copilot: intent detection, retrieval tools, memory, the LLM path and its fallback."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.chat import ChatMessage, ChatSession
from app.services import copilot, search_index
from app.services.llm import LLMError
from tests.test_dsa_services import JOBS, seed_jobs


def ask(client: TestClient, message: str, session_id: str | None = None) -> dict:
    response = client.post("/api/v1/copilot/chat", json={"message": message, "session_id": session_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_intent_detection_reads_keywords_in_one_pass():
    cases = {
        "How am I doing so far?": "stats",
        "which jobs should I apply to first": "recommend",
        "find remote python internships": "search",
        "What skills am I missing?": "skills",
        "Any update on my Zomato application?": "status",
        "Do I have interviews this week, any clashes?": "interviews",
        "how does the trie work here": "explain",
        "can you improve my resume bullets": "resume",
        "what can you do": "help",
        "pytorch bengaluru": "search",  # no keyword at all: treated as a search
        "ok": "help",
    }
    for message, expected in cases.items():
        assert copilot.detect_intent(message)["intent"] == expected, message
    # "this" must not count as "hi", and "skills" still counts for "skill"
    assert "hi" not in copilot.detect_intent("this is about skills")["keywords"]
    assert copilot.detect_intent("this is about skills")["intent"] == "skills"
    assert set(copilot.TOOLS) == set(copilot.INTENT_KEYWORDS) == set(copilot.SUGGESTIONS)


def test_chat_answers_from_the_users_data_without_an_llm(auth_client: TestClient, master_resume: dict):
    search_index.invalidate()
    seed_jobs()
    status = auth_client.get("/api/v1/copilot/status").json()
    assert status["mode"] == "rules" and status["llm_available"] is False and len(status["starters"]) >= 3

    stats = ask(auth_client, "How am I doing so far?")
    assert f"{len(JOBS)} jobs in total" in stats["message"]["content"] and "Applied: 2" in stats["message"]["content"]
    trace = stats["message"]["trace"]
    assert trace["intent"] == "stats" and trace["model"] == "rules" and trace["tool"] == "tool_stats"
    assert trace["memory_messages"] == 0 and stats["message"]["follow_ups"]
    session_id = stats["session"]["id"]
    assert stats["session"]["title"] == "How am I doing so far?"

    best = ask(auth_client, "Which jobs should I apply to first?", session_id)
    assert best["message"]["content"].startswith("Your best matches out of 6 waiting jobs")
    assert [card["match_score"] for card in best["message"]["cards"]][:2] == [88, 84]
    assert best["message"]["trace"]["memory_messages"] == 2  # the first exchange is now in the ring buffer

    found = ask(auth_client, "find kuberntes roles", session_id)
    assert "kubernetes" in found["message"]["content"] and "Flipkart" in found["message"]["content"]

    where = ask(auth_client, "any update on my meesho application?", session_id)
    assert "SDE Intern at Meesho: Applied" in where["message"]["content"] and "next:" in where["message"]["content"]

    skills = ask(auth_client, "what skills am I missing, and how do I learn rag?", session_id)
    assert "Cheapest path to RAG" in skills["message"]["content"] and "Python" in skills["message"]["content"]

    explain = ask(auth_client, "explain how the bloom filter is used", session_id)
    assert "Bloom filter" in explain["message"]["content"] and "backend/app/dsa/bloom_filter.py" in explain["message"]["content"]
    assert "can answer" not in explain["message"]["content"]

    interviews = ask(auth_client, "when is my next interview?", session_id)
    assert "no interviews coming up" in interviews["message"]["content"]

    assert "career copilot" in ask(auth_client, "what can you do", session_id)["message"]["content"]

    sessions = auth_client.get("/api/v1/copilot/sessions").json()["items"]
    assert len(sessions) == 1 and sessions[0]["messages"] == 16
    history = auth_client.get(f"/api/v1/copilot/sessions/{session_id}").json()["messages"]
    assert [m["role"] for m in history[:2]] == ["user", "assistant"] and history[0]["content"] == "How am I doing so far?"
    assert history[-1]["trace"]["memory_messages"] == copilot.MEMORY_MESSAGES  # capped: older messages dropped


def test_chat_with_an_empty_account(auth_client: TestClient):
    search_index.invalidate()
    assert "Nothing has been found yet" in ask(auth_client, "how many jobs so far?")["message"]["content"]
    assert "no jobs waiting" in ask(auth_client, "recommend the best jobs")["message"]["content"]
    assert "no jobs yet" in ask(auth_client, "find python internships")["message"]["content"]
    assert "haven't uploaded a resume" in ask(auth_client, "review my resume")["message"]["content"]
    assert "can't see a skill gap" in ask(auth_client, "what should I learn")["message"]["content"]


def test_llm_answers_from_retrieved_facts(auth_client: TestClient, master_resume: dict, fake_llm):
    search_index.invalidate()
    seed_jobs()
    provider = fake_llm({"CAREER COPILOT CHAT": {"answer": "Start with Zomato (88).", "follow_ups": ["Why Zomato?", ""]}})
    assert auth_client.get("/api/v1/copilot/status").json()["mode"] == "llm"
    first = ask(auth_client, "Which jobs should I apply to first?")
    assert first["message"]["content"] == "Start with Zomato (88)." and first["message"]["follow_ups"] == ["Why Zomato?"]
    assert first["message"]["trace"]["model"] == "fake"
    prompt = provider.calls[0]["prompt"]
    facts = json.loads(prompt.split("<facts>\n")[1].split("\n</facts>")[0])
    assert facts["best_matches"][0] == {"role": "Python Backend Intern", "company": "Zomato", "score": 88,
                                        "location": "Gurugram, India", "beats_percent_of_deck": facts["best_matches"][0]["beats_percent_of_deck"],
                                        "deadline": None}
    assert "(this is the first message)" in prompt and provider.calls[0]["schema"]["required"] == ["answer", "follow_ups"]
    assert "jane@example.com" not in prompt and "supersecret" not in prompt  # only the facts for this question are sent

    ask(auth_client, "and what about my skills?", first["session"]["id"])
    assert "user: Which jobs should I apply to first?" in provider.calls[1]["prompt"]  # memory reaches the model
    assert "assistant: Start with Zomato (88)." in provider.calls[1]["prompt"]


def test_llm_failure_falls_back_to_rules(auth_client: TestClient, master_resume: dict, fake_llm):
    search_index.invalidate()
    seed_jobs()
    fake_llm({}, fail=LLMError("model overloaded"))
    reply = ask(auth_client, "How am I doing so far?")
    assert "jobs in total" in reply["message"]["content"] and reply["message"]["trace"]["model"] == "rules"
    fake_llm({"CAREER COPILOT CHAT": {"answer": "   ", "follow_ups": []}})  # an empty answer is not an answer
    assert "jobs in total" in ask(auth_client, "How am I doing so far?")["message"]["content"]


def test_sessions_are_private_and_deletable(auth_client: TestClient, client: TestClient):
    mine = ask(auth_client, "what can you do")["session"]["id"]
    assert auth_client.post("/api/v1/copilot/chat", json={"message": "   "}).status_code == 422
    assert auth_client.post("/api/v1/copilot/chat", json={"message": "x" * 2001}).status_code == 422
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/copilot/chat", json={"message": "hi"}).status_code == 401
    client.post("/api/v1/auth/register", json={"email": "sam@example.com", "password": "supersecret1", "full_name": "Sam"})
    assert client.get(f"/api/v1/copilot/sessions/{mine}").status_code == 404
    assert client.delete(f"/api/v1/copilot/sessions/{mine}").status_code == 404
    other = ask(client, "hello", mine)  # someone else's session id starts a new conversation instead
    assert other["session"]["id"] != mine and client.get("/api/v1/copilot/sessions").json()["items"][0]["messages"] == 2
    assert client.delete(f"/api/v1/copilot/sessions/{other['session']['id']}").json() == {"deleted": True}
    with SessionLocal() as db:
        assert db.query(ChatSession).count() == 1 and db.query(ChatMessage).count() == 2  # messages went with it


def test_export_and_account_deletion_cover_conversations(auth_client: TestClient):
    ask(auth_client, "what can you do")
    exported = auth_client.get("/api/v1/users/me/export").json()
    assert len(exported["copilot_conversations"]) == 1 and len(exported["copilot_conversations"][0]["messages"]) == 2
    assert auth_client.request("DELETE", "/api/v1/users/me", json={"confirm": "DELETE"}).status_code in (200, 204)
    with SessionLocal() as db:
        assert db.query(ChatSession).count() == 0 and db.query(ChatMessage).count() == 0


def test_rate_limiter_is_per_user(monkeypatch):
    from app.api import copilot as api

    assert api.chat_limiter.limit == 20 and api.chat_limiter.window == 60
    api.chat_limiter.reset()
    assert all(api.chat_limiter.allow("u1") for _ in range(20)) and not api.chat_limiter.allow("u1")
    assert api.chat_limiter.allow("u2") and api.chat_limiter.retry_after("u1") > 0
    api.chat_limiter.reset()
