"""Ollama as an LLM provider (local, Docker or Ollama Cloud): request shape, JSON checking and repair,
clear errors, provider order, scan budget, embeddings, and the Settings › Integrations endpoints.
There is no real Ollama here: every call is answered by respx."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import config as config_module
from app.config import settings
from app.models.application import Application
from app.models.enums import ATSPlatform
from app.scrapers import SCRAPERS, SearchQuery
from app.scrapers.base import ScrapedJob
from app.services import ai_setup, embeddings, llm_schemas
from app.services import llm as llm_mod
from app.services.llm import (
    LLMClient,
    OllamaError,
    OllamaProvider,
    compact_json_blocks,
    fit_prompt,
    llm_budget,
    ollama_schema,
    set_llm,
)
from tests.conftest import FakeProvider

BASE = "http://ollama.test:11434"
EVAL = {"evaluation": {"match_score": 83, "skills_match": 18, "experience_match": 16, "industry_match": 14,
                       "location_match": 20, "compensation_match": 15, "proceed_with_application": True,
                       "reasoning": "Strong Python fit.", "missing_skills": [], "strong_matches": ["Python"]}}


def chat(content: Any, **extra: Any) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={"model": settings.ollama_model, "message": {"role": "assistant", "content": text},
                                     "done": True, "done_reason": "stop", "prompt_eval_count": 900, "eval_count": 80, **extra})


def sent(call: Any) -> dict[str, Any]:
    return json.loads(call.request.content)


@pytest.fixture(autouse=True)
def ollama_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", BASE)
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen3.5:4b")
    monkeypatch.setattr(settings, "OLLAMA_API_KEY", None)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(OllamaProvider, "_disabled_features", set())
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)  # retry back-off
    ai_setup._memory.clear()


def evaluate(provider: OllamaProvider | None = None) -> dict[str, Any]:
    out = (provider or OllamaProvider()).complete("SYSTEM", "TASK: JOB MATCH EVALUATION", llm_schemas.JOB_EVALUATION_SCHEMA, "low", None)
    return json.loads(out)


# ------------------------------------------------------------------ request + response
@respx.mock
def test_request_body_uses_native_chat_with_schema_and_small_context_options() -> None:
    route = respx.post(f"{BASE}/api/chat").mock(return_value=chat(EVAL))
    assert evaluate() == EVAL
    body = sent(route.calls.last)
    assert body["model"] == "qwen3.5:4b"
    assert body["stream"] is False
    assert body["think"] is False
    assert body["keep_alive"] == "30m"
    assert body["options"] == {"temperature": 0, "num_predict": 4096, "num_ctx": 8192}
    assert body["format"] == ollama_schema(llm_schemas.JOB_EVALUATION_SCHEMA)
    assert body["format"]["required"] == ["evaluation"] and "additionalProperties" not in json.dumps(body["format"])
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == "SYSTEM"
    assert '"match_score":"integer"' in body["messages"][1]["content"]  # the shape is in the prompt too
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
def test_cloud_sends_the_key_and_no_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "https://ollama.com")
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "gpt-oss:120b")
    monkeypatch.setattr(settings, "OLLAMA_API_KEY", "test-cloud-key")
    route = respx.post("https://ollama.com/api/chat").mock(
        return_value=chat("Here you go:\n```json\n" + json.dumps(EVAL) + "\n```"))
    assert evaluate() == EVAL
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer test-cloud-key"
    body = sent(route.calls.last)
    assert "format" not in body and "keep_alive" not in body and "num_ctx" not in body["options"]
    assert body["think"] == "low"  # gpt-oss takes a level; it can't switch reasoning off


@respx.mock
def test_reply_with_think_tags_and_missing_nested_keys_is_completed() -> None:
    partial = {"evaluation": {"skills_match": 18, "reasoning": "ok"}}
    respx.post(f"{BASE}/api/chat").mock(return_value=chat('<think>{"draft": 1}</think>' + json.dumps(partial)))
    out = evaluate()
    assert out["evaluation"]["skills_match"] == 18
    assert out["evaluation"]["missing_skills"] == [] and out["evaluation"]["proceed_with_application"] is False
    assert out["evaluation"]["location_match"] == 0


@respx.mock
def test_invalid_json_gets_one_repair_round() -> None:
    route = respx.post(f"{BASE}/api/chat").mock(side_effect=[chat("Sure! The candidate is a strong fit."), chat(EVAL)])
    assert evaluate() == EVAL
    assert route.call_count == 2
    repair = sent(route.calls[1])["messages"]
    assert [m["role"] for m in repair] == ["system", "user", "assistant", "user"]
    assert repair[2]["content"] == "Sure! The candidate is a strong fit."
    assert "could not be used" in repair[3]["content"] and "ONLY the complete JSON object" in repair[3]["content"]


@respx.mock
def test_missing_top_level_key_is_repaired_and_a_second_failure_raises() -> None:
    route = respx.post(f"{BASE}/api/chat").mock(side_effect=[chat({"score": 80}), chat(EVAL)])
    assert evaluate() == EVAL and route.call_count == 2
    assert 'missing key \\"evaluation\\"' in route.calls[1].request.content.decode()

    respx.post(f"{BASE}/api/chat").mock(side_effect=[chat("nope"), chat("still nope")])
    with pytest.raises(OllamaError, match="did not return the expected JSON"):
        evaluate()


@respx.mock
def test_model_not_pulled_says_how_to_get_it() -> None:
    respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(404, json={"error": "model 'qwen3.5:4b' not found"}))
    provider = OllamaProvider()
    with pytest.raises(OllamaError) as info:
        evaluate(provider)
    assert "`ollama pull qwen3.5:4b`" in str(info.value) and "Download model" in str(info.value)
    assert not provider.is_retryable(info.value)


@respx.mock
def test_connection_refused_says_ollama_is_not_running() -> None:
    respx.post(f"{BASE}/api/chat").mock(side_effect=httpx.ConnectError("Connection refused"))
    provider = OllamaProvider()
    with pytest.raises(OllamaError) as info:
        evaluate(provider)
    assert f"Ollama isn't running at {BASE}" in str(info.value) and "ollama serve" in str(info.value)
    assert provider.is_retryable(info.value)


@respx.mock
def test_truncated_reply_is_an_error() -> None:
    respx.post(f"{BASE}/api/chat").mock(return_value=chat('{"evaluation": {"match', done_reason="length"))
    with pytest.raises(OllamaError, match="stopped mid-answer"):
        evaluate()


@respx.mock
def test_busy_ollama_is_retried_then_answers() -> None:
    route = respx.post(f"{BASE}/api/chat").mock(
        side_effect=[httpx.Response(503, json={"error": "server busy, please try again"}), chat(EVAL)])
    client = LLMClient()
    assert client.provider_names == ["ollama"]
    assert client.complete_json("TASK: JOB MATCH EVALUATION", schema=llm_schemas.JOB_EVALUATION_SCHEMA) == EVAL
    assert route.call_count == 2


@respx.mock
def test_model_without_thinking_support_degrades() -> None:
    route = respx.post(f"{BASE}/api/chat").mock(
        side_effect=[httpx.Response(400, json={"error": '"llama3.2" does not support thinking'}), chat(EVAL)])
    assert evaluate() == EVAL
    assert "think" in sent(route.calls[0]) and "think" not in sent(route.calls[1])


@respx.mock
def test_small_context_shortens_the_job_description_but_keeps_the_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OLLAMA_NUM_CTX", 4096)
    route = respx.post(f"{BASE}/api/chat").mock(return_value=chat(EVAL))
    resume = {"skills": ["Python"] * 50}
    prompt = (f"TASK: JOB MATCH EVALUATION\n\n<master_resume>\n{json.dumps(resume, indent=2)}\n</master_resume>\n\n"
              f"<job>\n{'Build Python services. ' * 2000}\n</job>\n\nReturn ONLY the JSON object.")
    OllamaProvider().complete("S" * 4000, prompt, llm_schemas.JOB_EVALUATION_SCHEMA, None, None)
    user = sent(route.calls.last)["messages"][1]["content"]
    assert len(user) < len(prompt) / 3
    assert "shortened to fit" in user and user.startswith("TASK: JOB MATCH EVALUATION")
    assert "Return ONLY the JSON object." in user and '"evaluation"' in user
    assert '["Python","Python"' in user  # JSON blocks are compacted


def test_prompt_helpers() -> None:
    questions = json.dumps([{"question": f"Q{i}?" * 50} for i in range(40)], indent=2)
    prompt = f"Intro\n<job>\n{'x' * 20000}\n</job>\n<questions>\n{questions}\n</questions>\nEnd"
    compact = compact_json_blocks(prompt)
    assert "\n  " not in compact.split("<questions>")[1]
    fitted = fit_prompt(compact, 12000)
    assert compact.split("<questions>")[1] == fitted.split("<questions>")[1]  # questions are never cut
    assert fitted.startswith("Intro\n<job>\n") and fitted.endswith("End") and len(fitted) < len(compact)

    schema = {"$defs": {"Item": {"type": "object", "properties": {"a": {"type": ["string", "null"], "pattern": "x+"}},
                                 "required": ["a"], "additionalProperties": False}},
              "type": "object", "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/Item"}},
                                               "kind": {"anyOf": [{"type": "null"}, {"type": "string", "enum": ["a", "b"]}]},
                                               "code": {"type": "string", "pattern": "^[A-Z]{2}$", "format": "x"}},
              "required": ["items", "kind"], "additionalProperties": False}
    assert ollama_schema(schema) == {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}},
                       "kind": {"type": "string", "enum": ["a", "b"]},
                       "code": {"type": "string", "pattern": "^[A-Z]{2}$"}},
        "required": ["items", "kind"],
    }


# ------------------------------------------------------------------ provider choice + budget
def test_provider_selection(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    from app.services.llm import AnthropicProvider

    assert LLMClient().provider_names == ["ollama"]  # auto with only OLLAMA_MODEL
    assert LLMClient().primary == "ollama" and llm_mod.active_model(LLMClient()) == "qwen3.5:4b"

    monkeypatch.setattr(settings, "OLLAMA_MODEL", "")
    assert LLMClient().provider_names == []  # off unless configured

    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    assert LLMClient().provider_names == ["ollama"] and settings.ollama_model == "qwen3.5:4b"  # default model

    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen3.5:9b")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    client = LLMClient()
    assert client.provider_names == ["ollama", "anthropic"] and client.model_of("ollama") == "qwen3.5:9b"

    monkeypatch.setattr(settings, "LLM_PROVIDER", "auto")
    client = LLMClient()
    assert client.provider_names == ["anthropic", "ollama"]  # Claude stays first, Ollama is the last fallback
    assert isinstance(client.providers[0], AnthropicProvider) and client.model_of("anthropic") == settings.ANTHROPIC_MODEL

    monkeypatch.setattr(settings, "OLLAMA_MODEL", "")
    assert LLMClient().provider_names == ["anthropic"]  # Anthropic-only setups are unchanged

    monkeypatch.setattr(settings, "LLM_PROVIDER", "claude")
    with caplog.at_level(logging.WARNING, logger="app.services.llm"):
        assert LLMClient().provider_names == ["anthropic"]
    assert "Unknown LLM_PROVIDER" in caplog.text


def test_llm_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SCAN_LLM_CONCURRENCY", 6)
    monkeypatch.setattr(settings, "MAX_LLM_EVALUATIONS_PER_SCAN", 40)
    assert llm_budget(LLMClient()) == (1, 15)
    monkeypatch.setattr(settings, "OLLAMA_CONCURRENCY", 2)
    monkeypatch.setattr(settings, "OLLAMA_MAX_EVALUATIONS_PER_SCAN", 60)
    assert llm_budget(LLMClient()) == (2, 40)
    claude = FakeProvider()
    claude.name = "anthropic"
    assert llm_budget(LLMClient(providers=[claude, OllamaProvider()])) == (6, 40)
    assert llm_budget(LLMClient(providers=[])) == (6, 40)


def test_base_url_cleanup_and_docker_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434/api/")
    assert settings.ollama_base_url == "http://localhost:11434"
    monkeypatch.setattr(config_module, "in_container", lambda: True)
    assert settings.ollama_base_url == "http://host.docker.internal:11434"
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://ollama:11434")
    assert settings.ollama_base_url == "http://ollama:11434" and not settings.ollama_is_cloud
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "")
    assert settings.ollama_base_url == "http://host.docker.internal:11434"


# ------------------------------------------------------------------ scan with only Ollama
def _job(i: int) -> ScrapedJob:
    return ScrapedJob(company_name=f"Local {i}", role_title="Software Engineer Intern", location="Delhi, India",
                      description="Build Python and FastAPI services with PostgreSQL and Docker. " * 3,
                      source_url=f"https://local.example/jobs/{i}", source_platform=ATSPlatform.GREENHOUSE).finalize()


class _SixJobs:
    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        return [_job(i) for i in range(6)]


@respx.mock
def test_scan_with_only_ollama_scores_the_top_jobs_one_at_a_time(auth_client: TestClient, master_resume: dict,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.database import SessionLocal, session_scope
    from app.models.user import User
    from app.services import agent_orchestrator as orch

    monkeypatch.setattr(settings, "OLLAMA_MAX_EVALUATIONS_PER_SCAN", 3)
    monkeypatch.setitem(SCRAPERS, "local_board", _SixJobs)
    set_llm(LLMClient())
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def answer(request: httpx.Request) -> httpx.Response:
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.1)
        with lock:
            active["now"] -= 1
        return chat(EVAL)

    route = respx.post(f"{BASE}/api/chat").mock(side_effect=answer)
    with session_scope() as db:
        user = db.query(User).filter(User.email == "jane@example.com").one()
        run = orch.run_scan(db, user, platforms=["local_board"])
        assert run.status == "completed"
    assert route.call_count == 3 and active["peak"] == 1
    with SessionLocal() as db:
        methods = sorted(a.match_details["method"] for a in db.query(Application).all())
    assert methods == ["heuristic", "heuristic", "heuristic", "llm", "llm", "llm"]


# ------------------------------------------------------------------ embeddings
@respx.mock
def test_ollama_embeddings_are_padded_and_fall_back_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "ollama")
    route = respx.post(f"{BASE}/api/embed").mock(return_value=httpx.Response(200, json={"embeddings": [[0.6, 0.8], [1.0, 0.0]]}))
    a, b = embeddings.embed_texts(["python developer", "x" * 9000])
    assert len(a) == len(b) == settings.EMBEDDING_DIM
    assert a[:2] == [0.6, 0.8] and not any(a[2:])
    assert embeddings.cosine_similarity(a, b) == pytest.approx(0.6)
    body = sent(route.calls.last)
    assert body["model"] == "nomic-embed-text" and len(body["input"][1]) == embeddings.OLLAMA_EMBED_CHARS

    respx.post(f"{BASE}/api/embed").mock(return_value=httpx.Response(500, json={"error": "boom"}))
    assert embeddings.embed_texts(["python developer"]) == [embeddings.local_embedding("python developer")]


# ------------------------------------------------------------------ Settings › Integrations
@respx.mock
def test_integrations_shows_ollama_status_without_secrets(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OLLAMA_API_KEY", "secret-key-123")
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://user:pw@ollama.test:11434/api")
    set_llm(LLMClient())
    respx.get(f"{BASE}/api/version").mock(return_value=httpx.Response(200, json={"version": "0.35.0"}))
    respx.get(f"{BASE}/api/tags").mock(return_value=httpx.Response(200, json={"models": [{"name": "qwen3.5:4b"}, {"name": "llama3.2"}]}))
    r = auth_client.get("/api/v1/users/me/integrations")
    assert r.status_code == 200
    llm = r.json()["llm"]
    assert llm["provider"] == "ollama" and llm["providers"] == ["ollama"] and llm["model"] == "qwen3.5:4b"
    assert llm["ollama"] == {"configured": True, "base_url": BASE, "cloud": False, "model": "qwen3.5:4b", "reachable": True,
                             "version": "0.35.0", "model_pulled": True, "models": ["llama3.2:latest", "qwen3.5:4b"],
                             "error": None, "pull": None}
    assert "secret-key-123" not in r.text and "pw@" not in r.text


@respx.mock
def test_integrations_when_ollama_is_down_answers_quickly(auth_client: TestClient) -> None:
    respx.get(f"{BASE}/api/version").mock(side_effect=httpx.ConnectError("Connection refused"))
    started = time.monotonic()
    ollama = auth_client.get("/api/v1/users/me/integrations").json()["llm"]["ollama"]
    assert time.monotonic() - started < 2
    assert ollama["reachable"] is False and ollama["error"] == f"Ollama isn't running at {BASE}"
    assert ollama["model_pulled"] is None and ollama["models"] == []

    respx.get(f"{BASE}/api/version").mock(return_value=httpx.Response(200, json={"version": "0.35.0"}))
    respx.get(f"{BASE}/api/tags").mock(return_value=httpx.Response(200, json={"models": []}))
    ollama = auth_client.get("/api/v1/users/me/integrations").json()["llm"]["ollama"]
    assert ollama["reachable"] is True and ollama["model_pulled"] is False


@respx.mock
def test_llm_test_endpoint(auth_client: TestClient) -> None:
    r = auth_client.post("/api/v1/users/me/integrations/llm/test")
    assert r.json()["ok"] is False and "built-in heuristics" in r.json()["error"]  # nothing configured yet

    set_llm(LLMClient())
    route = respx.post(f"{BASE}/api/chat").mock(return_value=chat({"ok": True, "reply": "Ready to help with your applications!"}))
    out = auth_client.post("/api/v1/users/me/integrations/llm/test").json()
    assert out["ok"] is True and out["provider"] == "ollama" and out["model"] == "qwen3.5:4b"
    assert out["sample"] == "Ready to help with your applications!" and out["latency_ms"] >= 0
    assert "CONNECTION TEST" in sent(route.calls.last)["messages"][1]["content"]

    respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(404, json={"error": "model 'qwen3.5:4b' not found"}))
    out = auth_client.post("/api/v1/users/me/integrations/llm/test").json()
    assert out["ok"] is False and "isn't downloaded" in out["error"] and "Download model" in out["hint"]


def _wait_for_pull(client: TestClient) -> dict[str, Any]:
    for _ in range(100):
        progress = client.get("/api/v1/users/me/integrations/ollama/pull").json()
        if progress["status"] in ("success", "error"):
            return progress
        time.sleep(0.05)
    raise AssertionError("the download never finished")


def test_pull_runs_in_the_background_with_progress(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert auth_client.get("/api/v1/users/me/integrations/ollama/pull").json()["status"] == "idle"
    lines = [{"status": "pulling manifest"},
             {"status": "pulling aaa", "digest": "sha256:aaa", "total": 3000, "completed": 1000},
             {"status": "pulling bbb", "digest": "sha256:bbb", "total": 1000, "completed": 1000},
             {"status": "pulling aaa", "digest": "sha256:aaa", "total": 3000, "completed": 3000},
             {"status": "verifying sha256 digest"}, {"status": "writing manifest"}, {"status": "success"}]
    gate = threading.Event()

    def stream(request: httpx.Request) -> httpx.Response:
        gate.wait(5)
        return httpx.Response(200, content="\n".join(json.dumps(line) for line in lines).encode() + b"\n")

    with respx.mock:
        route = respx.post(f"{BASE}/api/pull").mock(side_effect=stream)
        r = auth_client.post("/api/v1/users/me/integrations/ollama/pull")
        assert r.status_code == 202 and r.json()["status"] == "pulling" and r.json()["model"] == "qwen3.5:4b"
        assert auth_client.post("/api/v1/users/me/integrations/ollama/pull").json()["status"] == "pulling"  # no second download
        gate.set()
        done = _wait_for_pull(auth_client)
        assert route.call_count == 1 and json.loads(route.calls.last.request.content) == {"model": "qwen3.5:4b", "stream": True}
    assert done["status"] == "success" and done["completed"] == done["total"] == 4000 and done["percent"] == 100

    with respx.mock:
        respx.post(f"{BASE}/api/pull").mock(return_value=httpx.Response(
            200, content=b'{"status":"pulling manifest"}\n{"error":"pull model manifest: file does not exist"}\n'))
        auth_client.post("/api/v1/users/me/integrations/ollama/pull")
        failed = _wait_for_pull(auth_client)
    assert failed["status"] == "error" and "file does not exist" in failed["error"]

    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "https://ollama.com")
    r = auth_client.post("/api/v1/users/me/integrations/ollama/pull")
    assert r.status_code == 400 and "nothing to download" in r.json()["detail"]


@respx.mock
def test_credentials_in_the_ollama_url_are_never_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A base URL like http://user:pass@host must not leak through Test AI, status or download errors."""
    secret_base = "http://qauser:qapass@127.0.0.1:11999"
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", secret_base)
    respx.post("http://127.0.0.1:11999/api/chat").mock(side_effect=httpx.ConnectError("Connection refused"))
    respx.get("http://127.0.0.1:11999/api/version").mock(return_value=httpx.Response(404))
    set_llm(LLMClient())
    result = ai_setup.connection_test()
    status = ai_setup.ollama_status()
    shown = json.dumps([result, status, ai_setup._pull_error(httpx.HTTPStatusError(
        "Client error '404' for url 'http://qauser:qapass@127.0.0.1:11999/api/pull'", request=None, response=None))])
    assert not result["ok"] and "127.0.0.1:11999" in result["error"]
    assert "qapass" not in shown and "qauser" not in shown


def test_metal_and_out_of_memory_hints() -> None:
    # Seen on a MacBook Air (Ollama 0.40.0): Metal failed to start; raising OLLAMA_NUM_CTX can't help
    message = ("Ollama error 500: llama-server startup failed after projector CPU offload retry: llama-server process has "
               "terminated: exit status 1: error: failed to initialize the Metal library error: failed to allocate context "
               "llama_init_from_model: failed to initialize the context")
    hint = ai_setup.hint_for(message)
    assert "Metal" in hint and "Raise OLLAMA_NUM_CTX" not in hint
    hint = ai_setup.hint_for("llama-server process has terminated: failed to allocate context (out of memory)")
    assert "doesn't fit" in hint and "qwen3.5:4b" in hint and "Raise OLLAMA_NUM_CTX" not in hint
    assert "Raise OLLAMA_NUM_CTX" in ai_setup.hint_for("The prompt is longer than the model's context (num_ctx)")
