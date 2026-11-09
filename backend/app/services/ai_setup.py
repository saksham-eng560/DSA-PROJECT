"""AI model status, a connection test and Ollama model downloads, for Settings › Integrations.

* :func:`llm_section` describes the active provider / model and probes Ollama with short timeouts
  (a stopped Ollama never holds the page up for long): version, downloaded models, and whether
  ``OLLAMA_MODEL`` is one of them. Only scheme + host of the URL are shown, never a key.
* :func:`connection_test` sends one tiny structured request through the configured client.
* :func:`start_pull` downloads ``OLLAMA_MODEL`` in a background thread and :func:`pull_progress`
  reports ``{status, completed, total, error}``. Progress lives in Redis when it's available (every
  API process sees it), otherwise in this process's memory.

API keys stay in ``.env``: nothing here accepts or returns one.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import httpx

from app.config import settings
from app.core.redis import get_redis
from app.services import llm_schemas
from app.services.llm import (
    LLMClient,
    LLMError,
    active_model,
    get_llm,
    ollama_error_text,
    ollama_headers,
    public_url,
    render_prompt,
    scrub_credentials,
)

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 2.0  # configured Ollama
DETECT_TIMEOUT_SECONDS = 0.5  # not configured: only notice an Ollama already running nearby
PULL_READ_TIMEOUT_SECONDS = 600.0  # longest silence allowed between two progress lines
PULL_KEY = "autoapply:ollama-pull:{model}"
PULL_TTL_SECONDS = 24 * 3600

_memory: dict[str, dict[str, Any]] = {}
_memory_lock = threading.Lock()
_start_lock = threading.Lock()


class PullError(Exception):
    """A model download could not start or failed."""


# --------------------------------------------------------------------------- helpers
def normalize_model(name: str) -> str:
    """``llama3.2`` -> ``llama3.2:latest`` (how Ollama lists a model pulled without a tag)."""
    name = name.strip()
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def ollama_status() -> dict[str, Any]:
    model = settings.ollama_model
    base = settings.ollama_base_url
    shown = public_url(base)
    configured = settings.ollama_enabled or settings.EMBEDDING_PROVIDER == "ollama"
    out: dict[str, Any] = {
        "configured": settings.ollama_enabled,
        "base_url": shown,
        "cloud": settings.ollama_cloud_model,
        "model": model or None,
        "reachable": False,
        "version": None,
        "model_pulled": None,
        "models": [],
        "error": None,
        "pull": None,
    }
    timeout = PROBE_TIMEOUT_SECONDS if configured else DETECT_TIMEOUT_SECONDS
    try:
        with httpx.Client(timeout=timeout, headers=ollama_headers()) as client:
            if not settings.ollama_is_cloud:
                version = client.get(f"{base}/api/version")
                version.raise_for_status()
                out["version"] = str(version.json().get("version") or "") or None
            tags = client.get(f"{base}/api/tags")
            if tags.status_code in (401, 403):
                out["reachable"] = True
                out["error"] = "Ollama Cloud rejected the API key: check OLLAMA_API_KEY in .env"
                return out
            tags.raise_for_status()
            listed = tags.json().get("models") or []
    except httpx.TimeoutException:
        out["error"] = f"No answer from Ollama at {shown} within {timeout:g} s"
        return out
    except httpx.TransportError:
        out["error"] = f"Ollama isn't running at {shown}"
        return out
    except (httpx.HTTPStatusError, ValueError, AttributeError) as exc:
        why = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        out["error"] = f"{shown} didn't answer like Ollama ({why})"  # never str(exc): it repeats the full URL
        return out
    names = sorted({normalize_model(str(m.get("name") or m.get("model"))) for m in listed
                    if isinstance(m, dict) and (m.get("name") or m.get("model"))})
    out["reachable"] = True
    out["models"] = names
    if model:
        pulled = normalize_model(model) in names
        # Ollama Cloud lists what it serves; nothing is downloaded there.
        out["model_pulled"] = pulled if (names or not settings.ollama_is_cloud) else None
    if model and not settings.ollama_is_cloud:
        progress = pull_progress(model)
        # A finished download only matters while the model is still there (it may have been removed since).
        if progress["status"] != "idle" and not (progress["status"] == "success" and not out["model_pulled"]):
            out["pull"] = progress
    return out


def llm_section(llm: LLMClient | None = None) -> dict[str, Any]:
    llm = llm or get_llm()
    return {
        "provider": llm.primary,
        "providers": llm.provider_names,
        "model": active_model(llm),
        "embedding_provider": settings.EMBEDDING_PROVIDER,
        "ollama": ollama_status(),
    }


# --------------------------------------------------------------------------- connection test
def hint_for(message: str) -> str:
    low = message.lower()
    if "isn't downloaded" in low or "ollama pull" in low:
        return f"Press Download model, or run `ollama pull {settings.ollama_model}` where Ollama runs."
    if "isn't running" in low or "could not reach" in low or "connection failed" in low:
        return ("Start Ollama (the Ollama app on a Mac, `ollama serve`, or `docker compose --profile ollama up -d` on a "
                "server) and check OLLAMA_BASE_URL in .env.")
    if any(word in low for word in ("api key", "api_key", "unauthorized", "authentication", "401", "signin")):
        return "Check the key in .env (ANTHROPIC_API_KEY, OPENAI_API_KEY or OLLAMA_API_KEY), then restart the app."
    # Ollama couldn't start the Mac GPU (Metal) at all: not a context or model-size problem.
    if "metal library" in low or "mtl0" in low or "metal backend" in low:
        return ("Ollama can't start the Mac's GPU (Metal), so no model loads. Run `ollama run <model> hi` to confirm, "
                "then restart the Mac and install the latest Ollama from https://ollama.com/download.")
    # Out of memory while loading: a bigger context would make it worse, so point at a smaller model / context.
    if any(word in low for word in ("failed to allocate", "out of memory", "more system memory", "insufficient memory",
                                    "cuda error", "llama-server process has terminated", "failed to initialize the context")):
        return (f"The model doesn't fit in this computer's memory. Set OLLAMA_MODEL=qwen3.5:4b (or llama3.2:3b) and "
                f"OLLAMA_NUM_CTX=4096 in .env, close other apps, quit and reopen Ollama, then restart. "
                f"(Now: {settings.ollama_model}, OLLAMA_NUM_CTX={settings.OLLAMA_NUM_CTX}.)")
    if "num_ctx" in low or "context" in low:
        return "Raise OLLAMA_NUM_CTX in .env (for example 16384), then restart."
    if "took longer" in low or "timed out" in low or "timeout" in low or "busy" in low:
        return ("The model is slow on this machine: pick a smaller one (llama3.2:3b) or raise OLLAMA_TIMEOUT_SECONDS. "
                "The first call after a restart also loads the model.")
    if "json" in low:
        return "The model answered, but not in the expected format. Small models sometimes do: try again, or a bigger model."
    return "See the API logs for details (docker compose logs api, or logs/api.log)."


def connection_test() -> dict[str, Any]:
    """One tiny structured request through the configured client."""
    llm = get_llm()
    result: dict[str, Any] = {"ok": False, "provider": llm.primary, "model": active_model(llm), "latency_ms": 0}
    if not llm.available:
        return {**result, "error": "No AI model is set up, so the agent uses its built-in heuristics.",
                "hint": "Add ANTHROPIC_API_KEY to .env, or set up a free model with Ollama (How to set it up), then restart."}
    started = time.monotonic()
    try:
        data, used = llm.complete_json_traced(render_prompt("connection_test"), schema=llm_schemas.CONNECTION_TEST_SCHEMA,
                                              effort="low", max_tokens=256, task="connection_test")
    except LLMError as exc:
        message = scrub_credentials(str(exc))
        if len(llm.providers) == 1:  # "All LLM providers failed: ollama: <why>" -> "<why>"
            message = message.removeprefix(f"All LLM providers failed: {llm.primary}: ")
        return {**result, "latency_ms": round((time.monotonic() - started) * 1000), "error": message[:800], "hint": hint_for(message)}
    reply = str(data.get("reply") or "").strip() or json.dumps(data, ensure_ascii=False)[:200]
    out = {"ok": True, "provider": used, "model": llm.model_of(used), "latency_ms": round((time.monotonic() - started) * 1000),
           "sample": reply[:300]}
    if used != llm.primary:
        out["hint"] = f"{llm.primary} didn't answer, so the fallback ({used}) did: see the API logs for why."
    return out


# --------------------------------------------------------------------------- model download
def _key(model: str) -> str:
    return PULL_KEY.format(model=normalize_model(model))


def _save(model: str, state: dict[str, Any]) -> None:
    record = {**state, "model": model, "updated_at": time.time()}
    redis = get_redis()
    if redis is not None:
        try:
            redis.set(_key(model), json.dumps(record), ex=PULL_TTL_SECONDS)
            return
        except Exception as exc:  # noqa: BLE001 - fall back to memory
            logger.warning("Could not store the download progress in Redis: %s", exc)
    with _memory_lock:
        _memory[_key(model)] = record


def _load(model: str) -> dict[str, Any] | None:
    redis = get_redis()
    if redis is not None:
        try:
            raw = redis.get(_key(model))
            if raw:
                return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read the download progress from Redis: %s", exc)
    with _memory_lock:
        record = _memory.get(_key(model))
        return dict(record) if record else None


def pull_progress(model: str | None = None) -> dict[str, Any]:
    model = (model or settings.ollama_model).strip()
    state = _load(model) if model else None
    if not state:
        return {"status": "idle", "model": model or None, "detail": None, "completed": 0, "total": 0, "percent": 0, "error": None}
    status = state.get("status") or "idle"
    error = state.get("error")
    if status == "pulling" and time.time() - float(state.get("updated_at") or 0) > PULL_READ_TIMEOUT_SECONDS + 60:
        status, error = "error", "The download stopped responding. Press Download model to try again."
    completed, total = int(state.get("completed") or 0), int(state.get("total") or 0)
    percent = 100 if status == "success" else (min(99, int(completed * 100 / total)) if total else 0)
    return {"status": status, "model": model, "detail": state.get("detail"), "completed": completed, "total": total,
            "percent": percent, "error": error}


def _pull_error(exc: Exception) -> str:
    shown = public_url(settings.ollama_base_url)
    if isinstance(exc, PullError):
        return str(exc)
    if isinstance(exc, httpx.ConnectError):
        return f"Ollama isn't running at {shown}"
    if isinstance(exc, httpx.TimeoutException):
        return "Ollama stopped sending progress for 10 minutes. Press Download model to try again."
    return scrub_credentials(f"Download failed: {exc}")


def _pull(model: str) -> None:
    state: dict[str, Any] = {"status": "pulling", "detail": "starting", "completed": 0, "total": 0, "error": None}
    layers: dict[str, tuple[int, int]] = {}
    last_saved = 0.0
    try:
        with httpx.stream("POST", f"{settings.ollama_base_url}/api/pull", json={"model": model, "stream": True},
                          headers=ollama_headers(), timeout=httpx.Timeout(PULL_READ_TIMEOUT_SECONDS, connect=10.0)) as response:
            if response.status_code >= 400:
                response.read()
                raise PullError(f"Ollama error {response.status_code}: {ollama_error_text(response)}")
            for line in response.iter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("error"):
                    raise PullError(f"Ollama: {event['error']}")
                digest = event.get("digest")
                if digest and event.get("total"):
                    layers[str(digest)] = (int(event.get("completed") or 0), int(event["total"]))
                state["detail"] = str(event.get("status") or "")
                state["completed"] = sum(done for done, _ in layers.values())
                state["total"] = sum(size for _, size in layers.values())
                if event.get("status") == "success":
                    state["status"] = "success"
                    break
                if time.monotonic() - last_saved >= 0.5:
                    _save(model, state)
                    last_saved = time.monotonic()
        if state["status"] != "success":
            raise PullError("The download ended before Ollama finished it. Press Download model to try again.")
        state["completed"] = state["total"]
        _save(model, state)
        logger.info("Ollama model %s downloaded", model)
    except Exception as exc:  # noqa: BLE001 - reported to the dashboard
        message = _pull_error(exc)
        logger.warning("Downloading Ollama model %s failed: %s", model, message)
        _save(model, {**state, "status": "error", "error": message})


def start_pull(model: str | None = None) -> dict[str, Any]:
    """Start downloading ``OLLAMA_MODEL`` (no-op while a download of it is already running)."""
    model = (model or settings.ollama_model).strip()
    if not model:
        raise PullError("Set OLLAMA_MODEL in .env first (for example qwen3.5:4b), then restart the app.")
    if settings.ollama_is_cloud:
        raise PullError("Ollama Cloud models run on ollama.com, so there is nothing to download.")
    with _start_lock:
        current = pull_progress(model)
        if current["status"] == "pulling":
            return current
        _save(model, {"status": "pulling", "detail": "starting", "completed": 0, "total": 0, "error": None})
    threading.Thread(target=_pull, args=(model,), name="ollama-pull", daemon=True).start()
    return pull_progress(model)
