import json

import pytest

from app.services import llm_schemas
from app.services.llm import LLMClient, LLMError, LLMRefusal, LLMUnavailable, extract_json, render_prompt, system_prompt
from tests.conftest import FakeProvider


def test_extract_json_variants() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Sure! Here it is: {"a": 3} hope that helps') == {"a": 3}
    with pytest.raises(LLMError):
        extract_json("no json here")
    with pytest.raises(LLMError):
        extract_json("[1, 2, 3]")


def test_render_prompt_substitutes_json_and_text() -> None:
    prompt = render_prompt("cover_letter", candidate_name="Jane", role_title="SWE", company_name="Acme",
                           job_description_text="JD", resume_json={"b": 1, "a": 2}, company_research="")
    assert "Jane" in prompt and "Acme" in prompt
    assert '"a": 2' in prompt  # dicts rendered as sorted JSON
    assert "{{" not in prompt


def test_system_prompt_contains_directives() -> None:
    sp = system_prompt()
    assert "ABSOLUTE TRUTHFULNESS" in sp and "USER SOVEREIGNTY" in sp and "JSON" in sp


def test_client_falls_back_to_second_provider() -> None:
    first = FakeProvider(fail=LLMError("boom"))
    second = FakeProvider({"TASK": {"ok": True}})
    client = LLMClient(providers=[first, second])
    assert client.complete_json("TASK: x") == {"ok": True}
    assert len(first.calls) == 1 and len(second.calls) == 1


def test_client_refusal_moves_to_next_provider() -> None:
    first = FakeProvider(fail=LLMRefusal("declined"))
    second = FakeProvider({"TASK": {"ok": 1}})
    assert LLMClient(providers=[first, second]).complete_json("TASK") == {"ok": 1}


def test_client_unavailable_without_providers() -> None:
    with pytest.raises(LLMUnavailable):
        LLMClient(providers=[]).complete_json("x")
    with pytest.raises(LLMUnavailable):
        LLMClient(providers=[FakeProvider(fail=LLMError("x"))]).complete_json("x")


def test_retry_policy_for_retryable_errors(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("app.services.llm.time.sleep", lambda s: sleeps.append(s))

    class Flaky(FakeProvider):
        def __init__(self) -> None:
            super().__init__({"T": {"done": True}})
            self.n = 0

        def complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.n += 1
            if self.n < 3:
                raise ConnectionError("temporary")
            return super().complete(*args, **kwargs)

        def is_retryable(self, exc: Exception) -> bool:
            return isinstance(exc, ConnectionError)

    provider = Flaky()
    assert LLMClient(providers=[provider]).complete_json("T") == {"done": True}
    assert sleeps == [1, 2]  # 1s, 2s backoff (third attempt succeeds)


def _walk(schema: dict) -> None:
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        for sub in schema["properties"].values():
            _walk(sub)
    if schema.get("type") == "array":
        _walk(schema["items"])


@pytest.mark.parametrize("name", [n for n in dir(llm_schemas) if n.endswith("_SCHEMA")])
def test_structured_output_schemas_are_strict(name: str) -> None:
    schema = getattr(llm_schemas, name)
    json.dumps(schema)
    _walk(schema)


def test_anthropic_provider_request_shape(monkeypatch) -> None:
    """The Claude request uses structured outputs, effort, cached system prompt and refusal fallback."""
    from app.services import llm as llm_mod

    captured = {}

    class _Msg:
        stop_reason = "end_turn"
        content = [type("B", (), {"type": "text", "text": '{"ok": true}'})()]
        usage = None

    class _Stream:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *a):  # type: ignore[no-untyped-def]
            return False

        def get_final_message(self):  # type: ignore[no-untyped-def]
            return _Msg()

    class _Beta:
        class messages:  # noqa: N801
            @staticmethod
            def stream(**kwargs):  # type: ignore[no-untyped-def]
                captured.update(kwargs)
                return _Stream()

    provider = llm_mod.AnthropicProvider.__new__(llm_mod.AnthropicProvider)
    import anthropic

    provider._anthropic = anthropic
    provider.client = type("C", (), {"beta": _Beta(), "messages": _Beta.messages})()
    monkeypatch.setattr(llm_mod.settings, "ANTHROPIC_REFUSAL_FALLBACK", True)
    out = provider.complete("SYS", "PROMPT", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, "low", None)
    assert json.loads(out) == {"ok": True}
    assert captured["model"] == llm_mod.settings.ANTHROPIC_MODEL
    assert captured["fallbacks"] == "default"
    assert captured["betas"] == ["server-side-fallback-2026-07-01"]
    assert captured["output_config"]["effort"] == "low"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in captured and "temperature" not in captured


def test_anthropic_provider_refusal(monkeypatch) -> None:
    from app.services import llm as llm_mod

    class _Msg:
        stop_reason = "refusal"
        stop_details = type("D", (), {"category": "cyber"})()
        content: list = []

    provider = llm_mod.AnthropicProvider.__new__(llm_mod.AnthropicProvider)
    import anthropic

    provider._anthropic = anthropic
    monkeypatch.setattr(provider, "_request", lambda *a, **k: _Msg())
    with pytest.raises(LLMRefusal):
        provider.complete("s", "p", None, None, None)
