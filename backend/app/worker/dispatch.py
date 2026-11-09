"""Task dispatch that works with or without a Celery worker.

* With Redis + a running worker: ``apply_async`` on the named Celery task.
* ``CELERY_TASK_ALWAYS_EAGER=true`` (or no broker reachable): run in a local background thread
  so API requests never block on long jobs.

``after_commit=session`` defers dispatch until the surrounding DB transaction commits, so the
worker always sees the rows the task depends on.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# Tasks that drive a browser (or tailor with the AI) and can take minutes each. They get their own workers, so a
# batch of kept jobs being prepared never holds a scan at "Starting the scan…". Celery routes them to the "browser"
# queue (celery_app.task_routes); without Redis they run in their own thread pool.
BROWSER_TASKS = ("prepare_application", "stage_application", "submit_application", "linkedin_sync_user")
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="autoapply-task")
_browser_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="autoapply-browser")
_registry: dict[str, Callable[..., Any]] = {}
_inline = {"enabled": False}


def register(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _registry[name] = fn
        return fn

    return decorator


def _load_registry() -> None:
    if not _registry:
        from app.worker import tasks_apply, tasks_calendar, tasks_email, tasks_scan, tasks_sync  # noqa: F401


def use_local_execution() -> bool:
    return settings.CELERY_TASK_ALWAYS_EAGER or get_redis() is None


class run_inline:  # noqa: N801 - context manager used by tests / scripts
    """Execute dispatched tasks synchronously in the calling thread."""

    def __enter__(self) -> None:
        _inline["enabled"] = True

    def __exit__(self, *exc: Any) -> None:
        _inline["enabled"] = False


def _run_local(name: str, args: tuple[Any, ...], countdown: int | None) -> None:
    _load_registry()
    fn = _registry[name]

    def job() -> None:
        try:
            fn(*args)
        except Exception:
            logger.exception("Local task %s failed", name)

    if _inline["enabled"]:
        if not countdown:  # delayed retries are dropped in inline mode
            fn(*args)
        return
    pool = _browser_executor if name in BROWSER_TASKS else _executor
    if countdown:
        # A retry due later waits on a timer, not in the pool: it must not hold one of the few browser workers. The
        # timer never keeps the app from stopping: every delayed task is a submission, which start-up queues again.
        timer = threading.Timer(countdown, _submit_quietly, args=(pool, job))
        timer.daemon = True
        timer.start()
        return
    pool.submit(job)


def _submit_quietly(pool: ThreadPoolExecutor, job: Callable[[], None]) -> None:
    try:
        pool.submit(job)
    except RuntimeError:  # the app is shutting down
        pass


def _send(name: str, args: tuple[Any, ...], countdown: int | None) -> None:
    if _inline["enabled"] or use_local_execution():
        _run_local(name, args, countdown)
        return
    from app.worker.celery_app import celery_app

    celery_app.send_task(f"autoapply.{name}", args=list(args), countdown=countdown)


_PENDING = "pending_tasks"
_HOOKED = "pending_tasks_hooked"


def _send_pending(session: Session) -> None:
    for name, args, countdown in session.info.pop(_PENDING, []):
        _send(name, args, countdown)


def _drop_pending(session: Session) -> None:
    session.info.pop(_PENDING, None)  # the rows those tasks needed were never saved: nothing to run


def enqueue(name: str, *args: Any, countdown: int | None = None, after_commit: Session | None = None) -> None:
    """Run task ``name`` now, or once ``after_commit`` commits. Deferred tasks wait in the session's ``info``; one
    pair of hooks per session sends them on commit and drops them on a rollback, so a scan that queues many tasks
    doesn't keep a listener for each, and a task for a change that was rolled back never runs."""
    if after_commit is None or not after_commit.in_transaction():
        _send(name, args, countdown)
        return
    after_commit.info.setdefault(_PENDING, []).append((name, args, countdown))
    if not after_commit.info.get(_HOOKED):
        event.listen(after_commit, "after_commit", _send_pending)
        event.listen(after_commit, "after_rollback", _drop_pending)
        after_commit.info[_HOOKED] = True
