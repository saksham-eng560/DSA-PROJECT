"""LLM access layer.

* Primary provider: Anthropic Claude (``ANTHROPIC_MODEL``, default ``claude-opus-5-5``) with
  structured JSON outputs, adaptive thinking/effort control and server-side refusal fallback.
* Secondary provider: OpenAI chat-completions (``OPENAI_API_KEY``), used when Claude errors out.
* Free provider: Ollama (``OLLAMA_MODEL``), a model on your own computer / server or on Ollama
  Cloud, through Ollama's native ``/api/chat`` with JSON-schema constrained output.
* ``LLM_PROVIDER`` picks the order: ``auto`` (default) tries Anthropic, then OpenAI, then Ollama,
  whichever is configured; ``anthropic`` / ``openai`` / ``ollama`` put that one first.
* When no provider is configured, :class:`LLMUnavailable` is raised and every service falls back
  to deterministic heuristics so the product still works offline.

Retry policy (PLAN.md §13): LLM API error -> 3 attempts with 1s/2s/4s backoff, then fall back to
the next provider.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config import LLM_PROVIDERS, settings

logger = logging.getLogger(__name__)

JSON_RULE = (
    "\n\n═══════════════════════════════════════════════════════════════════\n"
    "OUTPUT FORMAT\n"
    "═══════════════════════════════════════════════════════════════════\n"
    "Every task you receive through this API expects a single JSON object as the complete "
    "response. Do not wrap it in markdown fences and do not add commentary before or after it."
)

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """Generic LLM failure."""


class LLMUnavailable(LLMError):
    """No LLM provider is configured (or all providers failed)."""


class LLMRefusal(LLMError):
    """The model declined the request."""


# --------------------------------------------------------------------------- prompts
@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    path = Path(settings.PROMPTS_DIR) / f"{name}.txt"
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **variables: Any) -> str:
    template = load_prompt(name)

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        value = variables.get(key, "")
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str)
        return "" if value is None else str(value)

    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", _sub, template)


def system_prompt() -> str:
    return load_prompt("master_system") + JSON_RULE


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object out of a model response, tolerating fences or stray prose."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("Model response did not contain JSON") from None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model response contained invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("Model response JSON was not an object")
    return value


def scrub_credentials(text: str) -> str:
    """Remove ``user:password@`` from any URL inside an error message before it's shown or logged."""
    return re.sub(r"(//)[^/@\s'\"]+:[^/@\s'\"]*@", r"\1", text)


def public_url(url: str) -> str:
    """Scheme + host (+ port) only: no path, query or credentials (safe to show and log)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    return urlunsplit((parts.scheme, f"{host}:{port}" if port else host, "", "", ""))


# --------------------------------------------------------------------------- providers
class Provider(Protocol):
    name: str

    def complete(
        self, system: str, prompt: str, schema: dict[str, Any] | None, effort: str | None, max_tokens: int | None
    ) -> str: ...


class AnthropicProvider:
    """Claude via the official ``anthropic`` SDK (streaming + ``get_final_message``)."""

    name = "anthropic"

    @property
    def model(self) -> str:
        return settings.ANTHROPIC_MODEL

    # Optional request features we can drop if a configured model rejects them.
    _disabled_features: set[str] = set()
    _features_lock = threading.Lock()

    def __init__(self) -> None:
        import anthropic

        self._anthropic = anthropic
        # We implement the retry policy ourselves (1s/2s/4s) so the SDK should not retry too.
        self.client = anthropic.Anthropic(
            api_key=settings.ANTHROPIC_API_KEY, timeout=settings.LLM_TIMEOUT_SECONDS, max_retries=0
        )

    def _disable(self, feature: str) -> None:
        with self._features_lock:
            self._disabled_features.add(feature)
        logger.warning("Anthropic model %s rejected feature '%s'; disabling it", settings.ANTHROPIC_MODEL, feature)

    def _request(
        self, system: str, prompt: str, schema: dict[str, Any] | None, effort: str | None, max_tokens: int | None
    ) -> Any:
        disabled = self._disabled_features
        output_config: dict[str, Any] = {}
        if effort and "effort" not in disabled:
            output_config["effort"] = effort
        if schema and "format" not in disabled:
            output_config["format"] = {"type": "json_schema", "schema": schema}

        kwargs: dict[str, Any] = {
            "model": settings.ANTHROPIC_MODEL,
            "max_tokens": max_tokens or settings.ANTHROPIC_MAX_TOKENS,
            # The master prompt is stable across calls -> cache it.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": prompt}],
        }
        if output_config:
            kwargs["output_config"] = output_config

        if settings.ANTHROPIC_REFUSAL_FALLBACK and "fallbacks" not in disabled:
            stream = self.client.beta.messages.stream(
                betas=[REFUSAL_FALLBACK_BETA], fallbacks="default", **kwargs
            )
        else:
            stream = self.client.messages.stream(**kwargs)
        with stream as s:
            return s.get_final_message()

    def complete(
        self, system: str, prompt: str, schema: dict[str, Any] | None, effort: str | None, max_tokens: int | None
    ) -> str:
        anthropic = self._anthropic
        for _ in range(4):  # at most one downgrade per optional feature
            try:
                message = self._request(system, prompt, schema, effort, max_tokens)
                break
            except anthropic.BadRequestError as exc:
                text = str(exc).lower()
                if "fallback" in text and "fallbacks" not in self._disabled_features:
                    self._disable("fallbacks")
                elif ("output_config.format" in text or "json_schema" in text or "schema" in text) and schema and (
                    "format" not in self._disabled_features
                ):
                    self._disable("format")
                elif "effort" in text and "effort" not in self._disabled_features:
                    self._disable("effort")
                else:
                    raise
        else:  # pragma: no cover - defensive
            raise LLMError("Anthropic request could not be completed")

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise LLMRefusal(f"Claude declined the request ({getattr(details, 'category', None)})")
        if message.stop_reason == "max_tokens":
            raise LLMError("Claude response was truncated (max_tokens)")
        text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")
        if not text.strip():
            raise LLMError("Claude returned an empty response")
        usage = getattr(message, "usage", None)
        if usage is not None:
            logger.debug(
                "claude usage in=%s out=%s cache_read=%s",
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
                getattr(usage, "cache_read_input_tokens", None),
            )
        return text

    def is_retryable(self, exc: Exception) -> bool:
        a = self._anthropic
        if isinstance(exc, (a.RateLimitError, a.APIConnectionError, a.APITimeoutError, a.InternalServerError)):
            return True
        return isinstance(exc, a.APIStatusError) and exc.status_code >= 500


class OpenAIProvider:
    """OpenAI chat-completions over REST (secondary provider)."""

    name = "openai"

    @property
    def model(self) -> str:
        return settings.OPENAI_MODEL

    def complete(
        self, system: str, prompt: str, schema: dict[str, Any] | None, effort: str | None, max_tokens: int | None
    ) -> str:
        body: dict[str, Any] = {
            "model": settings.OPENAI_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": min(max_tokens or 8000, 16000),
        }
        if schema is not None:
            body["response_format"] = {"type": "json_object"}
        response = httpx.post(
            f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            json=body,
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            raise LLMError(f"OpenAI error {response.status_code}: {response.text[:300]}")
        data = response.json()
        return data["choices"][0]["message"]["content"] or ""

    def is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, httpx.TransportError):
            return True
        return isinstance(exc, LLMError) and bool(re.search(r"error (429|5\d\d)", str(exc)))


# --------------------------------------------------------------------------- Ollama
CHARS_PER_TOKEN = 4  # rough estimate, used to keep prompts inside a small local context window
OLLAMA_MAX_PREDICT = 4096  # output cap per call
_SMALL_OUTPUT_RESERVE = 1536  # tokens kept free for the answer to a short task (scores, letters, answers)
_LARGE_SCHEMA_CHARS = 1500  # schemas bigger than this (a whole resume) produce long answers
_BLOCK_RE = re.compile(r"<([a-z_]+)>\n(.*?)\n</\1>", re.DOTALL)
# Blocks that must reach the model whole: every question / form field needs an answer.
_PROTECTED_BLOCKS = frozenset({"questions", "form_fields", "candidate", "interview_details", "user_preferences"})
_MIN_BLOCK_CHARS = 1200
_TRUNCATION_MARK = "\n[…shortened to fit the model's context window]"
_THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL)
_SCHEMA_TYPES = ("object", "array", "string", "integer", "number", "boolean")


class OllamaError(LLMError):
    """An Ollama call failed; ``retryable`` marks transient failures (busy, 5xx, connection)."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def compact_json_blocks(prompt: str) -> str:
    """Re-serialise JSON inside ``<tag>`` blocks without indentation: same content, fewer tokens."""

    def compact(match: re.Match[str]) -> str:
        body = match.group(2)
        if not body.lstrip().startswith(("{", "[")):
            return match.group(0)
        try:
            value = json.loads(body)
        except ValueError:
            return match.group(0)
        return f"<{match.group(1)}>\n{json.dumps(value, ensure_ascii=False, separators=(',', ':'))}\n</{match.group(1)}>"

    return _BLOCK_RE.sub(compact, prompt)


def fit_prompt(prompt: str, max_chars: int) -> str:
    """Shorten the biggest ``<tag>`` blocks (job description, resume, e-mail…) until the prompt fits
    in ``max_chars``. The instructions around the blocks and the protected blocks are kept whole."""
    for _ in range(12):
        excess = len(prompt) - max_chars
        if excess <= 0:
            break
        candidates = [m for m in _BLOCK_RE.finditer(prompt)
                      if m.group(1) not in _PROTECTED_BLOCKS and len(m.group(2)) > _MIN_BLOCK_CHARS + len(_TRUNCATION_MARK)]
        if not candidates:
            break
        biggest = max(candidates, key=lambda m: len(m.group(2)))
        body = biggest.group(2)
        keep = max(_MIN_BLOCK_CHARS, len(body) - excess - len(_TRUNCATION_MARK))
        shortened = body[:keep].rstrip() + _TRUNCATION_MARK
        start, end = biggest.span(2)
        prompt = prompt[:start] + shortened + prompt[end:]
    return prompt


def ollama_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A simplified copy of a JSON schema for Ollama's ``format``: only type / properties / required /
    items / enum (and anchored patterns) survive; ``$ref`` is resolved and unions collapse to their
    first non-null branch, since Ollama's grammar converter skips or mishandles the rest."""
    defs = {**(schema.get("definitions") or {}), **(schema.get("$defs") or {})}

    def simplify(node: Any, depth: int = 0) -> dict[str, Any]:
        if not isinstance(node, dict) or depth > 16:
            return {}
        ref = node.get("$ref")
        if isinstance(ref, str):
            target = defs.get(ref.rsplit("/", 1)[-1])
            return simplify(target, depth + 1) if target is not None else {}
        for union in ("anyOf", "oneOf", "allOf"):
            branches = [b for b in node.get(union) or [] if isinstance(b, dict) and b.get("type") != "null"]
            if branches and "type" not in node and "properties" not in node:
                return simplify(branches[0], depth + 1)
        out: dict[str, Any] = {}
        kind = node.get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), None)
        if kind in _SCHEMA_TYPES:
            out["type"] = kind
        properties = node.get("properties")
        if isinstance(properties, dict):
            out.setdefault("type", "object")
            out["properties"] = {key: simplify(sub, depth + 1) for key, sub in properties.items()}
            out["required"] = [key for key in node.get("required") or [] if key in properties]
        if isinstance(node.get("items"), dict):
            out["items"] = simplify(node["items"], depth + 1)
        if isinstance(node.get("enum"), list) and node["enum"]:
            out["enum"] = list(node["enum"])
        pattern = node.get("pattern")
        if isinstance(pattern, str) and pattern.startswith("^") and pattern.endswith("$"):
            out["pattern"] = pattern
        return out

    return simplify(schema)


def schema_shape(schema: dict[str, Any]) -> Any:
    """A compact example of the JSON a schema describes, to show the model in the prompt."""
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        return {key: schema_shape(sub) for key, sub in (schema.get("properties") or {}).items()}
    if kind == "array":
        return [schema_shape(schema.get("items") or {})]
    if schema.get("enum"):
        return " | ".join(str(v) for v in schema["enum"])
    return kind if isinstance(kind, str) else "any"


def _shape_instruction(schema: dict[str, Any]) -> str:
    return ("\n\nReply with ONE JSON object of exactly this shape (every key is required; use \"\", 0, "
            "false or [] when you have nothing to put there):\n"
            + json.dumps(schema_shape(schema), ensure_ascii=False, separators=(",", ":")))


def shape_problems(value: dict[str, Any], schema: dict[str, Any] | None) -> list[str]:
    """What's wrong with a parsed reply: missing top-level keys or a list / object of the wrong kind."""
    if not schema:
        return []
    problems: list[str] = []
    properties = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in value:
            problems.append(f'missing key "{key}"')
            continue
        expected = (properties.get(key) or {}).get("type")
        if expected == "object" and not isinstance(value[key], dict):
            problems.append(f'"{key}" must be a JSON object')
        elif expected == "array" and not isinstance(value[key], list):
            problems.append(f'"{key}" must be a JSON list')
    return problems


def _empty(schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        return fill_defaults({}, schema)
    return {"array": [], "string": "", "integer": 0, "number": 0, "boolean": False}.get(kind or "", "")


def fill_defaults(value: Any, schema: dict[str, Any]) -> Any:
    """Add empty values ("" / [] / 0 / false) for keys a small model left out, the convention the
    schemas in ``llm_schemas`` use for "nothing here"."""
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        if not isinstance(value, dict):
            return fill_defaults({}, schema)
        out = dict(value)
        for key, sub in (schema.get("properties") or {}).items():
            out[key] = fill_defaults(out[key], sub) if key in out else _empty(sub)
        return out
    if kind == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [fill_defaults(item, schema["items"]) for item in value]
    return value


def ollama_headers() -> dict[str, str]:
    """``Authorization`` for Ollama Cloud. The key only ever goes to OLLAMA_BASE_URL."""
    return {"Authorization": f"Bearer {settings.OLLAMA_API_KEY}"} if settings.OLLAMA_API_KEY else {}


def ollama_error_text(response: httpx.Response) -> str:
    """Ollama errors are ``{"error": "..."}``; anything else is shown as (short) text."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:300].strip()
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])[:300]
    return response.text[:300].strip()


_ollama_slots: dict[int, threading.BoundedSemaphore] = {}
_ollama_slots_lock = threading.Lock()


def ollama_slots() -> threading.BoundedSemaphore:
    """Process-wide limit on Ollama calls in flight (``OLLAMA_CONCURRENCY``), shared by scoring,
    tailoring, cover letters and answers so a CPU-only server is never asked for more at once."""
    size = max(1, settings.OLLAMA_CONCURRENCY)
    with _ollama_slots_lock:
        if size not in _ollama_slots:
            _ollama_slots[size] = threading.BoundedSemaphore(size)
        return _ollama_slots[size]


class OllamaProvider:
    """Ollama's native ``/api/chat`` (local Ollama, Ollama in Docker, or Ollama Cloud).

    * JSON output is constrained with a simplified JSON schema in ``format`` (not on cloud models,
      which don't support it), the shape is shown in the prompt too, and the reply is checked: an
      unusable reply gets one repair round before the call fails.
    * Prompts are compacted and, for a small context window, the biggest inputs are shortened.
    * At most ``OLLAMA_CONCURRENCY`` calls run at once in this process.
    """

    name = "ollama"

    # Optional request features we can drop if a configured model rejects them.
    _disabled_features: set[str] = set()
    _features_lock = threading.Lock()

    @property
    def model(self) -> str:
        return settings.ollama_model

    @property
    def base_url(self) -> str:
        return settings.ollama_base_url

    @property
    def shown_url(self) -> str:
        """The base URL for messages: never user:password@ or a path."""
        return public_url(self.base_url)

    def _disable(self, feature: str) -> None:
        with self._features_lock:
            self._disabled_features.add(feature)
        logger.warning("Ollama model %s rejected '%s'; continuing without it", self.model, feature)

    # ------------------------------------------------------------------ request
    def _num_predict(self, max_tokens: int | None) -> int:
        return max(64, min(max_tokens or OLLAMA_MAX_PREDICT, OLLAMA_MAX_PREDICT))

    def _think(self, effort: str | None) -> bool | str:
        if self.model.lower().startswith("gpt-oss"):
            # gpt-oss can't switch reasoning off; it takes a level instead.
            if not settings.OLLAMA_THINK:
                return "low"
            return {"xhigh": "high", "max": "high"}.get(effort or "", effort if effort in ("low", "medium", "high") else "medium")
        return bool(settings.OLLAMA_THINK)

    def _prepare_prompt(self, system: str, prompt: str, schema: dict[str, Any] | None, num_predict: int) -> str:
        prompt = compact_json_blocks(prompt)
        if schema is not None:
            prompt += _shape_instruction(schema)
        if settings.ollama_cloud_model:  # big context on the cloud: nothing to shorten
            return prompt
        num_ctx = settings.OLLAMA_NUM_CTX
        large = schema is not None and len(json.dumps(schema)) > _LARGE_SCHEMA_CHARS
        reserve = min(num_predict, num_ctx // 3 if large else _SMALL_OUTPUT_RESERVE)
        budget = num_ctx - reserve - estimate_tokens(system) - 64
        fitted = fit_prompt(prompt, max(budget, 0) * CHARS_PER_TOKEN)
        if fitted != prompt:
            logger.info("Shortened a prompt from ~%s to ~%s tokens to fit OLLAMA_NUM_CTX=%s",
                        estimate_tokens(prompt), estimate_tokens(fitted), num_ctx)
        if estimate_tokens(fitted) > budget:
            logger.warning("Prompt (~%s tokens + ~%s system) may not fit OLLAMA_NUM_CTX=%s; raise OLLAMA_NUM_CTX in .env",
                           estimate_tokens(fitted), estimate_tokens(system), num_ctx)
        return fitted

    def _body(self, messages: list[dict[str, str]], schema: dict[str, Any] | None, effort: str | None,
              num_predict: int) -> dict[str, Any]:
        cloud = settings.ollama_cloud_model
        options: dict[str, Any] = {"temperature": 0, "num_predict": num_predict}
        if not cloud:
            options["num_ctx"] = settings.OLLAMA_NUM_CTX
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False, "options": options}
        if "think" not in self._disabled_features:
            body["think"] = self._think(effort)
        if schema is not None and not cloud and "format" not in self._disabled_features:
            body["format"] = ollama_schema(schema)
        if not settings.ollama_is_cloud:
            body["keep_alive"] = settings.OLLAMA_KEEP_ALIVE
        return body

    def _http_error(self, response: httpx.Response) -> OllamaError:
        status = response.status_code
        detail = ollama_error_text(response)
        low = detail.lower()
        if status == 404 and "not found" in low and "model" in low:
            if settings.ollama_is_cloud:
                return OllamaError(f"Ollama Cloud has no model '{self.model}' ({detail}) — pick one from "
                                   "https://ollama.com/search?c=cloud and set OLLAMA_MODEL in .env", status)
            return OllamaError(f"The Ollama model '{self.model}' isn't downloaded yet — press \"Download model\" in "
                               f"Settings › Integrations or run `ollama pull {self.model}`", status)
        if status == 404:
            return OllamaError(f"Ollama answered 404 at {self.shown_url}/api/chat ({detail or 'not found'}) — check OLLAMA_BASE_URL", status)
        if status in (401, 403):
            if settings.ollama_is_cloud:
                return OllamaError(f"Ollama Cloud rejected the request ({detail}) — put a key from https://ollama.com/settings/keys "
                                   "in OLLAMA_API_KEY in .env, then restart", status)
            return OllamaError(f"Ollama refused the request ({detail}) — cloud models need `ollama signin` on the machine "
                               "running Ollama (or OLLAMA_API_KEY)", status)
        if status == 400 and "context" in low:
            return OllamaError(f"The prompt is longer than the model's context ({detail}) — raise OLLAMA_NUM_CTX in .env "
                               f"(now {settings.OLLAMA_NUM_CTX})", status)
        if status == 429:
            return OllamaError(f"Ollama rate limit ({detail}) — the free Ollama Cloud plan runs one request at a time", status, retryable=True)
        if status >= 500:
            return OllamaError(f"Ollama error {status}: {detail}", status, retryable=True)
        return OllamaError(f"Ollama error {status}: {detail}", status)

    def _chat(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/api/chat"
        timeout = httpx.Timeout(settings.OLLAMA_TIMEOUT_SECONDS, connect=10.0)
        for _ in range(3):  # at most one downgrade per optional feature
            try:
                response = httpx.post(url, json=body, headers=ollama_headers(), timeout=timeout)
            except httpx.ConnectError as exc:
                if settings.ollama_is_cloud:
                    raise OllamaError(f"Could not reach Ollama Cloud at {self.shown_url} ({exc})", retryable=True) from exc
                raise OllamaError(f"Ollama isn't running at {self.shown_url} — start the Ollama app or `ollama serve` "
                                  "(and check OLLAMA_BASE_URL)", retryable=True) from exc
            except httpx.ConnectTimeout as exc:
                raise OllamaError(f"Could not reach Ollama at {self.shown_url} (connection timed out) — check OLLAMA_BASE_URL",
                                  retryable=True) from exc
            except httpx.TimeoutException as exc:
                raise OllamaError(f"Ollama took longer than {settings.OLLAMA_TIMEOUT_SECONDS:.0f} s to answer — use a smaller "
                                  "model or raise OLLAMA_TIMEOUT_SECONDS") from exc
            except httpx.TransportError as exc:
                raise OllamaError(f"Ollama connection failed: {exc}", retryable=True) from exc
            if response.status_code == 400:
                text = ollama_error_text(response).lower()
                if "think" in text and "think" in body:
                    self._disable("think")
                    body.pop("think")
                    continue
                if ("format" in text or "schema" in text) and "format" in body:
                    self._disable("format")
                    body.pop("format")
                    continue
            if response.status_code >= 400:
                raise self._http_error(response)
            try:
                data = response.json()
            except ValueError as exc:
                raise OllamaError(f"{self.shown_url} did not answer like Ollama — check OLLAMA_BASE_URL") from exc
            if not isinstance(data, dict):
                raise OllamaError(f"{self.shown_url} did not answer like Ollama — check OLLAMA_BASE_URL")
            return data
        raise OllamaError("Ollama request could not be completed")

    def _reply(self, data: dict[str, Any]) -> str:
        prompt_tokens, output_tokens = data.get("prompt_eval_count") or 0, data.get("eval_count") or 0
        logger.debug("ollama %s in=%s out=%s took=%.1fs", self.model, prompt_tokens, output_tokens,
                     (data.get("total_duration") or 0) / 1e9)
        num_ctx = settings.OLLAMA_NUM_CTX
        if not settings.ollama_cloud_model and prompt_tokens + output_tokens >= num_ctx * 0.95:
            logger.warning("Ollama context is full (%s prompt + %s output tokens of OLLAMA_NUM_CTX=%s): part of the "
                           "input may have been dropped; raise OLLAMA_NUM_CTX", prompt_tokens, output_tokens, num_ctx)
        if data.get("done_reason") == "length":
            raise OllamaError(f"Ollama ({self.model}) stopped mid-answer at its length limit — raise OLLAMA_NUM_CTX "
                              f"(now {num_ctx}) or use a model with a bigger context")
        message = data.get("message") or {}
        return _THINK_TAGS.sub("", str(message.get("content") or "")).strip()

    @staticmethod
    def _parse(text: str, schema: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
        try:
            value = extract_json(text)
        except LLMError as exc:
            return None, [str(exc) if text else "the reply was empty"]
        return value, shape_problems(value, schema)

    def complete(
        self, system: str, prompt: str, schema: dict[str, Any] | None, effort: str | None, max_tokens: int | None
    ) -> str:
        if not self.model:
            raise OllamaError("Set OLLAMA_MODEL in .env to use Ollama")
        num_predict = self._num_predict(max_tokens)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": self._prepare_prompt(system, prompt, schema, num_predict)}]
        slots = ollama_slots()
        if not slots.acquire(timeout=settings.OLLAMA_TIMEOUT_SECONDS):
            raise OllamaError("Ollama is still busy with other requests (OLLAMA_CONCURRENCY) — try again shortly")
        try:
            text = self._reply(self._chat(self._body(messages, schema, effort, num_predict)))
            value, problems = self._parse(text, schema)
            if problems:  # one repair round: show the model its reply and ask for valid JSON only
                logger.warning("Ollama %s reply was unusable (%s); asking again", self.model, "; ".join(problems))
                fix = ("Your previous reply could not be used: " + "; ".join(problems) + ". Reply again with ONLY the "
                       "complete JSON object: no explanations, no markdown fences.")
                if schema is not None:
                    fix += _shape_instruction(schema)
                repair = [*messages, {"role": "assistant", "content": text[:4000] or "(empty)"}, {"role": "user", "content": fix}]
                text = self._reply(self._chat(self._body(repair, schema, effort, num_predict)))
                value, problems = self._parse(text, schema)
        finally:
            slots.release()
        required = (schema or {}).get("required") or []
        if value is None or (problems and required and not any(key in value for key in required)):
            raise OllamaError(f"Ollama ({self.model}) did not return the expected JSON: {'; '.join(problems)}")
        if schema is not None:
            value = fill_defaults(value, schema)
        return json.dumps(value, ensure_ascii=False)

    def is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, OllamaError):
            return exc.retryable
        return isinstance(exc, httpx.TransportError) and not isinstance(exc, httpx.ReadTimeout)


# --------------------------------------------------------------------------- client
def configured_providers() -> list[Any]:
    """Providers in the order of ``LLM_PROVIDER``: ``auto`` = Anthropic -> OpenAI -> Ollama (whichever
    is configured); a named provider goes first and the other configured ones stay as fallbacks."""
    raw = (settings.LLM_PROVIDER or "").strip().lower() or "auto"
    if raw not in LLM_PROVIDERS:
        logger.warning("Unknown LLM_PROVIDER=%r (expected auto, anthropic, openai or ollama); using auto", settings.LLM_PROVIDER)
    choice = settings.llm_provider
    configured = {"anthropic": bool(settings.ANTHROPIC_API_KEY), "openai": bool(settings.OPENAI_API_KEY),
                  "ollama": settings.ollama_enabled}
    factories: dict[str, Any] = {"anthropic": AnthropicProvider, "openai": OpenAIProvider, "ollama": OllamaProvider}
    order = ["anthropic", "openai", "ollama"]
    if choice != "auto":
        order.remove(choice)
        order.insert(0, choice)
        if not configured[choice]:
            logger.warning("LLM_PROVIDER=%s but it isn't configured (no API key); using the other providers", choice)
    return [factories[name]() for name in order if configured[name]]


class LLMClient:
    def __init__(self, providers: list[Any] | None = None) -> None:
        if providers is None:
            providers = configured_providers()
        self.providers = providers

    @property
    def available(self) -> bool:
        return bool(self.providers)

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self.providers]

    @property
    def primary(self) -> str | None:
        """Name of the provider tried first (``None`` = heuristics only)."""
        return self.providers[0].name if self.providers else None

    def model_of(self, name: str | None) -> str | None:
        """The model a provider uses (``None`` for an unknown provider)."""
        for provider in self.providers:
            if provider.name == name:
                return getattr(provider, "model", None) or None
        return None

    def _call_with_retries(self, provider: Any, call: Any) -> str:
        delays = [1, 2, 4][: max(settings.LLM_MAX_RETRIES - 1, 0)]
        attempt = 0
        while True:
            try:
                return call()
            except LLMRefusal:
                raise
            except Exception as exc:
                retryable = getattr(provider, "is_retryable", lambda e: False)(exc)
                if not retryable or attempt >= len(delays):
                    raise
                logger.warning("%s call failed (%s); retrying in %ss", provider.name, exc, delays[attempt])
                time.sleep(delays[attempt])
                attempt += 1

    def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any] | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        task: str = "task",
    ) -> dict[str, Any]:
        return self.complete_json_traced(prompt, schema, effort, max_tokens, task)[0]

    def complete_json_traced(
        self,
        prompt: str,
        schema: dict[str, Any] | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        task: str = "task",
    ) -> tuple[dict[str, Any], str]:
        """Like :meth:`complete_json`, and also returns the name of the provider that answered."""
        if not self.providers:
            raise LLMUnavailable("No LLM provider configured (set ANTHROPIC_API_KEY, OPENAI_API_KEY or OLLAMA_MODEL)")
        system = system_prompt()
        errors: list[str] = []
        for provider in self.providers:
            try:
                text = self._call_with_retries(
                    provider,
                    lambda p=provider: p.complete(
                        system, prompt, schema, effort or settings.ANTHROPIC_EFFORT, max_tokens
                    ),
                )
                return extract_json(text), provider.name
            except Exception as exc:  # noqa: BLE001 - try the next provider
                logger.warning("LLM provider %s failed on %s: %s", provider.name, task, exc)
                errors.append(f"{provider.name}: {exc}")
        raise LLMUnavailable("All LLM providers failed: " + " | ".join(errors))


_client: LLMClient | None = None
_client_lock = threading.Lock()


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = LLMClient()
    return _client


def set_llm(client: LLMClient | None) -> None:
    """Override the process-wide client (tests / scripts)."""
    global _client
    _client = client


def active_model(client: LLMClient | None = None) -> str | None:
    """The model of the provider tried first (``None`` = heuristics only)."""
    client = client or get_llm()
    return client.model_of(client.primary)


def llm_budget(client: LLMClient | None = None) -> tuple[int, int]:
    """(LLM calls at once, LLM evaluations per scan) for a scan. A local model on a CPU is slow, so
    with Ollama first the scan uses ``OLLAMA_CONCURRENCY`` / ``OLLAMA_MAX_EVALUATIONS_PER_SCAN`` when lower."""
    client = client or get_llm()
    concurrency, evaluations = settings.SCAN_LLM_CONCURRENCY, settings.MAX_LLM_EVALUATIONS_PER_SCAN
    if client.primary == "ollama":
        concurrency = min(concurrency, settings.OLLAMA_CONCURRENCY)
        evaluations = min(evaluations, settings.OLLAMA_MAX_EVALUATIONS_PER_SCAN)
    return max(1, concurrency), max(0, evaluations)
