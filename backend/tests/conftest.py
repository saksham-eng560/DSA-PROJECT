"""Shared test fixtures.

Environment is configured before any ``app`` import so settings pick it up. Tests run against
SQLite by default; set ``TEST_DATABASE_URL`` to run the same suite against PostgreSQL + pgvector.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="autoapply-tests-")
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_TMP}/test.db"),
        "REDIS_URL": "",
        "CELERY_TASK_ALWAYS_EAGER": "true",
        "LOCAL_STORAGE_PATH": f"{_TMP}/storage",
        "HUMAN_EMULATION": "false",
        "SECRET_KEY": "test-secret-key-that-is-long-enough-1234567890",
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "LLM_PROVIDER": "auto",
        "OLLAMA_MODEL": "",  # Ollama stays off unless a test configures it
        "OLLAMA_API_KEY": "",
        "EMBEDDING_PROVIDER": "local",
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
        "SMTP_HOST": "",
        "PROXY_URLS": "",
        "CAPTCHA_API_KEY": "",
        "AUTO_STAGE_APPLICATIONS": "true",
        "SUBMISSION_DRY_RUN": "false",
        "BROWSER_TIMEOUT_MS": "15000",
    }
)

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, SessionLocal, create_all, engine
from app.services.llm import LLMClient, set_llm
from app.services.rate_limiter import rate_limiter

FIXTURES = Path(__file__).parent / "fixtures"

SAMPLE_RESUME_TEXT = """JANE DOE
jane.doe@example.com | +1 (415) 555-0100 | linkedin.com/in/janedoe | github.com/janedoe
San Francisco, CA

SUMMARY
Backend engineer with 4 years of experience building Python APIs and data pipelines.

EXPERIENCE
Software Engineer | Acme Corp | San Francisco, CA   Jan 2022 - Present
• Built REST APIs in Python and FastAPI serving 2M requests/day
• Migrated PostgreSQL databases and reduced p95 latency by 40%
• Containerized services with Docker and deployed them on Kubernetes (AWS EKS)
Software Engineering Intern | Beta Labs | Remote   Jun 2021 - Aug 2021
• Wrote React dashboards in TypeScript for internal analytics

EDUCATION
University of California, Berkeley | B.S. Computer Science   2017 - 2021
• GPA: 3.8

PROJECTS
JobBot | Python, Playwright
• Automated browser workflows with Playwright and Docker

SKILLS
Python, TypeScript, SQL, FastAPI, PostgreSQL, Docker, Kubernetes, AWS, Git, React
"""


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    Base.metadata.drop_all(bind=engine)
    create_all()
    # PostgreSQL: pooled connections keep prepared plans for the dropped enum types ("cached plan must
    # not change result type"), so start every test with fresh connections.
    engine.dispose()
    rate_limiter._mem.clear()
    set_llm(LLMClient(providers=[]))  # heuristic mode unless a test injects a fake provider
    yield
    set_llm(None)


@pytest.fixture(autouse=True)
def _browser_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """The submit paths ask whether Chromium starts (a real launch); tests drive fake submitters, so it does
    unless a test says otherwise."""
    from app.automation import browser

    monkeypatch.setattr(browser, "browser_problem", lambda probe=True: None)


@pytest.fixture
def db() -> Iterator[Any]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    r = client.post("/api/v1/auth/register", json={"email": "jane@example.com", "password": "supersecret1", "full_name": "Jane Doe"})
    assert r.status_code == 201, r.text
    return client


@pytest.fixture
def master_resume(auth_client: TestClient) -> dict[str, Any]:
    r = auth_client.post("/api/v1/resumes/from-text", json={"text": SAMPLE_RESUME_TEXT})
    assert r.status_code == 201, r.text
    return r.json()


class FakeProvider:
    """Deterministic LLM provider: returns canned JSON per task keyword, records prompts."""

    name = "fake"

    def __init__(self, responses: dict[str, Any] | None = None, fail: Exception | None = None) -> None:
        self.responses = responses or {}
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def complete(self, system: str, prompt: str, schema: Any, effort: Any, max_tokens: Any) -> str:
        self.calls.append({"prompt": prompt, "schema": schema, "effort": effort})
        if self.fail:
            raise self.fail
        for key, value in self.responses.items():
            if key in prompt:
                return value if isinstance(value, str) else json.dumps(value)
        raise AssertionError(f"FakeProvider has no response for prompt: {prompt[:120]}")

    def is_retryable(self, exc: Exception) -> bool:
        return False


@pytest.fixture
def fake_llm() -> Any:
    def install(responses: dict[str, Any], fail: Exception | None = None) -> FakeProvider:
        provider = FakeProvider(responses, fail)
        set_llm(LLMClient(providers=[provider]))
        return provider

    return install
