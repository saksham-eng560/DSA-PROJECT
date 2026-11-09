"""A scan never waits behind browser work: preparing, filling and submitting kept jobs run in their own pool."""

from __future__ import annotations

import threading
from typing import Any

from app.worker import dispatch


def test_a_scan_starts_while_every_browser_worker_is_busy(monkeypatch: Any) -> None:
    dispatch._load_registry()
    release, started = threading.Event(), threading.Event()
    busy = []

    def slow_prepare(app_id: str) -> None:  # a kept job being filled in a browser for minutes
        busy.append(app_id)
        release.wait(10)

    monkeypatch.setitem(dispatch._registry, "prepare_application", slow_prepare)
    monkeypatch.setitem(dispatch._registry, "scan_user", lambda *a: started.set())
    monkeypatch.setattr(dispatch, "use_local_execution", lambda: True)
    try:
        for i in range(12):  # far more than every worker (seen when 20 kept jobs resumed after a restart)
            dispatch.enqueue("prepare_application", f"app-{i}")
        dispatch.enqueue("scan_user", "user-1")
        assert started.wait(5), "the scan waited behind preparations"
        assert len(busy) <= dispatch._browser_executor._max_workers
    finally:
        release.set()


def test_a_delayed_retry_never_holds_a_browser_worker(monkeypatch: Any) -> None:
    """Retries due later (a daily limit reached, another worker already sending it) used to sleep inside the pool:
    a few of them stopped every other job from being filled or sent."""
    dispatch._load_registry()
    ran = threading.Event()
    monkeypatch.setitem(dispatch._registry, "submit_application", lambda app_id: app_id == "now" and ran.set())
    monkeypatch.setattr(dispatch, "use_local_execution", lambda: True)
    for i in range(dispatch._browser_executor._max_workers + 2):
        dispatch.enqueue("submit_application", f"later-{i}", countdown=3600)
    dispatch.enqueue("submit_application", "now")
    assert ran.wait(5), "a submission waited behind delayed retries"


def test_celery_sends_browser_work_to_its_own_queue() -> None:
    from app.worker.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert {name for name in routes} == {f"autoapply.{n}" for n in dispatch.BROWSER_TASKS}
    assert all(r == {"queue": "browser"} for r in routes.values())
    assert "autoapply.scan_user" not in routes  # scans stay on the default queue's own worker


def test_tasks_wait_for_the_commit_with_one_hook_per_session(monkeypatch: Any) -> None:
    """Every deferred task added its own after_commit listener, never removed: a scan queueing hundreds of tasks
    kept hundreds of listeners."""
    from app.core.database import SessionLocal

    sent: list[tuple] = []
    monkeypatch.setattr(dispatch, "_send", lambda name, args, countdown: sent.append((name, args)))
    with SessionLocal() as db:
        db.connection()  # a transaction is open
        hooks = len(db.dispatch.after_commit)
        for i in range(100):
            dispatch.enqueue("prepare_application", str(i), after_commit=db)
        assert sent == [] and len(db.dispatch.after_commit) <= hooks + 1
        db.commit()
        assert [args for _name, args in sent] == [(str(i),) for i in range(100)]
        db.connection()
        dispatch.enqueue("submit_application", "x", after_commit=db)
        db.commit()
        assert len(sent) == 101 and len(db.dispatch.after_commit) <= hooks + 1  # nothing sent twice


def test_a_rolled_back_change_queues_no_task(monkeypatch: Any) -> None:
    """After a rollback (a scan's error handler), tasks queued for the change that was never saved still ran on the
    next commit, e.g. preparing an application whose "kept" status was rolled back."""
    from app.core.database import SessionLocal

    sent: list[tuple] = []
    monkeypatch.setattr(dispatch, "_send", lambda name, args, countdown: sent.append((name, args)))
    with SessionLocal() as db:
        db.connection()
        dispatch.enqueue("prepare_application", "rolled-back", after_commit=db)
        db.rollback()
        db.connection()
        dispatch.enqueue("scan_user", "kept", after_commit=db)
        db.commit()
    assert sent == [("scan_user", ("kept",))]
    dispatch.enqueue("scan_user", "now")  # no session: sent right away
    assert sent[-1] == ("scan_user", ("now",))
