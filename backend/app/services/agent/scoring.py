"""Scoring new jobs against your resume. The cheap checks come first (your hard filters, rule #2); the best
matches by resume similarity go to the LLM, the rest get the instant heuristic. In Swipe Review nothing is skipped
for a low score; otherwise only matches at or above ``auto_apply_threshold`` go on (rule #8 orders them)."""

from __future__ import annotations

import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from types import SimpleNamespace
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import checkpoint
from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.models.job import Job
from app.models.resume import Resume
from app.models.user import User
from app.services.agent.jobs import already_applied_elsewhere
from app.services.agent.review import NEAR_MISS, deck_target, is_swipe_mode, waiting_count
from app.services.agent.runs import RunLog
from app.services.application_service import set_status
from app.services.embeddings import cosine_similarity
from app.services.intern_level import with_resume
from app.services.job_matcher import evaluate_match, split_reasons
from app.services.llm import get_llm, llm_budget
from app.services.scan_progress import ScanProgress


def auto_apply_threshold(prefs: dict[str, Any]) -> int:
    return int(prefs.get("auto_apply_threshold") or 80)


def held_back(app: Application) -> bool:
    """A near miss skipped only because Swipe Review was full when it was found (not by you, not by a hard rule):
    a later scan that finds it again may still show it, when the deck is thin."""
    return (app.status == ApplicationStatus.SKIPPED and app.review_decision is None
            and bool((app.match_details or {}).get("held_back")))


def revive_held_back(db: Session, user: User, new_apps: list[Application], found_again: list[Application],
                     master: Resume | None) -> list[Application]:
    """The held-back near misses of ``found_again`` that now fit in a thin deck, to be scored with ``new_apps``."""
    held = [a for a in found_again if held_back(a)]
    if not held:
        return []
    picked = plan_fill(db, user, new_apps + held, master)
    revived = [a for a in held if a.id in picked]
    for app in revived:  # back on the deck; scoring decides the rest as for a new posting
        set_status(db, app, ApplicationStatus.DISCOVERED, "agent", "Found again while Swipe Review has room")
    return revived


def plan_fill(db: Session, user: User, apps: list[Application], master: Resume | None) -> set[uuid.UUID]:
    """The near misses (best first, in ``apps`` order) that fill Swipe Review up to ``fill_deck_to``."""
    prefs = with_resume(user.prefs, master.parsed_content if master else None)
    target = deck_target(prefs)
    if not is_swipe_mode(prefs) or target == 0:
        return set()
    fits, near = 0, []
    for app in apps:
        fixed, relaxable, _soft = split_reasons(app.job, prefs)
        if fixed:
            continue
        if relaxable:
            near.append((len(relaxable), app))
        else:
            fits += 1
    room = target - waiting_count(db, user, {a.id for a in apps}) - fits
    if room <= 0 or not near:
        return set()
    near.sort(key=lambda pair: pair[0])  # stable: fewest misses first, then the order given (best similarity)
    return {app.id for _n, app in near[:room]}


def _precheck(db: Session, user: User, app: Application, master: Resume | None,
              relax: bool = False) -> tuple[bool, list[str]]:
    """Cheap checks before scoring. Returns (needs_scoring, heads_up).

    ``relax``: a near miss picked to fill a thin Swipe Review; your narrower wishes (tech focus, length,
    verified companies only) become heads-ups instead of skipping it."""
    job = app.job
    prefs = with_resume(user.prefs, master.parsed_content if master else None)  # your graduation year
    swipe = is_swipe_mode(prefs)
    fixed, relaxable, soft = split_reasons(job, prefs)
    relaxed = relax and swipe and not fixed and bool(relaxable)
    reason = next(iter(fixed or ([] if relaxed else relaxable) or ([] if swipe else soft)), None)
    if reason:
        app.match_score = 0
        app.match_reasoning = reason
        if swipe and not fixed and relaxable and not relaxed:  # a near miss the deck had no room for this time
            app.match_details = {"near_miss": True, "held_back": True}
        set_status(db, app, ApplicationStatus.SKIPPED, "agent", reason)
        return False, []
    if already_applied_elsewhere(db, user, job):
        app.match_reasoning = "Already applied to the same role at this company"
        set_status(db, app, ApplicationStatus.SKIPPED, "agent", app.match_reasoning)
        return False, []
    heads_up = ([f"{NEAR_MISS}{r}" for r in relaxable] if relaxed else []) + (soft if swipe else [])
    if master is None:
        app.match_reasoning = "Upload a master resume to enable matching"
        if heads_up:
            app.match_details = {"heads_up": heads_up, "near_miss": relaxed}
        return False, heads_up
    return True, heads_up


def _apply_evaluation(db: Session, user: User, app: Application, evaluation: dict[str, Any], heads_up: list[str],
                      resume_vec: list[float] | None) -> None:
    prefs = user.prefs
    threshold = auto_apply_threshold(prefs)
    if resume_vec is not None and app.job.description_embedding:
        app.similarity_score = round(cosine_similarity(resume_vec, app.job.description_embedding), 4)
    app.match_score = evaluation["match_score"]
    app.match_reasoning = evaluation["reasoning"]
    app.match_details = {**evaluation, "heads_up": heads_up,
                         "near_miss": any(h.startswith(NEAR_MISS) for h in heads_up)}
    if is_swipe_mode(prefs):
        # Never skipped for a low score: you decide in Swipe Review.
        set_status(db, app, ApplicationStatus.MATCHED, "agent", f"Match score {app.match_score} — waiting for your swipe")
    elif evaluation["proceed_with_application"] and evaluation["match_score"] >= threshold:
        set_status(db, app, ApplicationStatus.MATCHED, "agent", f"Match score {app.match_score}")
    else:
        set_status(db, app, ApplicationStatus.SKIPPED, "agent", f"Match score {app.match_score} below {threshold}")


def evaluate_application(
    db: Session, user: User, app: Application, master: Resume | None, use_llm: bool = True,
    resume_vec: list[float] | None = None, relax: bool = False,
) -> Application:
    needs_scoring, heads_up = _precheck(db, user, app, master, relax)
    if not needs_scoring or master is None:
        return app
    evaluation = evaluate_match(master.parsed_content, app.job, user.prefs, auto_apply_threshold(user.prefs), use_llm=use_llm)
    _apply_evaluation(db, user, app, evaluation, heads_up, resume_vec)
    return app


_SNAPSHOT_SKIP = {"description_embedding"}


def job_snapshot(job: Job) -> Any:
    """A plain, session-free copy of a job, safe to read from another thread."""
    return SimpleNamespace(**{attr.key: getattr(job, attr.key) for attr in inspect(Job).column_attrs
                              if attr.key not in _SNAPSHOT_SKIP})


def swiped(db: Session, app: Application) -> bool:
    """Re-read the decision: you may have swiped this card while the scan was still working."""
    db.refresh(app, attribute_names=["status", "review_decision"])
    return app.review_decision is not None


def score_applications(db: Session, user: User, apps: list[Application], master: Resume | None,
                       resume_vec: list[float] | None, run: RunLog, progress: ScanProgress | None = None) -> None:
    """Score new jobs. The best MAX_LLM_EVALUATIONS_PER_SCAN (by resume similarity) go to the LLM,
    SCAN_LLM_CONCURRENCY at a time; the rest use the instant heuristic meanwhile. Cards reach Swipe
    Review as they're scored, and a card you swipe mid-scan is left alone."""

    def advance() -> None:
        if progress is not None:
            progress.scored += 1
            progress.tick()

    relax = plan_fill(db, user, apps, master)
    if relax:
        run.log(f"Swipe Review would be thin: showing {len(relax)} near miss{'es' if len(relax) != 1 else ''} too, "
                "each marked with the setting it misses")

    def score_now(app: Application, use_llm: bool = False) -> None:
        if not swiped(db, app):  # you kept / skipped it before the scan got to it
            try:
                evaluate_application(db, user, app, master, use_llm=use_llm, resume_vec=resume_vec,
                                     relax=app.id in relax)
            except Exception as exc:  # noqa: BLE001
                run.log(f"Evaluation failed for {app.job.company_name}: {exc}", level="error")

    # With a local model (Ollama) first, llm_budget() lowers both numbers. Even one call at a time goes
    # through the pool below, so the long tail gets its instant score while the LLM works.
    concurrency, llm_count = llm_budget()
    parallel = master is not None and get_llm().available
    if not parallel:
        for idx, app in enumerate(apps):
            use_llm = idx < llm_count
            if use_llm or idx % 10 == 0:
                checkpoint(db)  # cards reach Swipe Review as they're scored; no write lock held during an LLM call
            score_now(app, use_llm)
            advance()
        return

    assert master is not None
    prefs = user.prefs
    threshold = auto_apply_threshold(prefs)
    to_llm: list[tuple[Application, list[str], Any]] = []
    for app in apps[:llm_count]:
        try:
            needs, heads_up = (False, []) if swiped(db, app) else _precheck(db, user, app, master, app.id in relax)
        except Exception as exc:  # noqa: BLE001
            run.log(f"Evaluation failed for {app.job.company_name}: {exc}", level="error")
            needs, heads_up = False, []
        if needs:
            to_llm.append((app, heads_up, job_snapshot(app.job)))
        else:
            advance()
    checkpoint(db)  # no write lock is held while the LLM works
    budget = max(1, int(settings.SCAN_LLM_BUDGET_SECONDS))
    deadline = time.monotonic() + budget
    if progress is not None:
        progress.llm_total, progress.llm_concurrency, progress.llm_deadline = len(to_llm), concurrency, deadline
    pool = ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(to_llm))),
                              thread_name_prefix="scan-score")
    futures = {pool.submit(evaluate_match, master.parsed_content, snap, prefs, threshold, use_llm=True): (app, heads_up)
               for app, heads_up, snap in to_llm}
    try:
        for idx, app in enumerate(apps[llm_count:]):  # the long tail: instant heuristic while the LLM works
            if idx % 10 == 0:
                checkpoint(db)
            score_now(app)
            advance()
        checkpoint(db)
        pending = set(futures)
        while pending:
            if time.monotonic() >= deadline:
                # The AI is too slow on this machine: the rest keep an instant score instead of holding the scan.
                run.log(f"The AI scored {len(futures) - len(pending)} of {len(futures)} jobs within {budget}s; "
                        f"the other {len(pending)} get an instant score (SCAN_LLM_BUDGET_SECONDS, or a faster model)",
                        level="warning")
                for future in pending:
                    future.cancel()
                    app, _heads_up = futures[future]
                    score_now(app)
                    if progress is not None:
                        progress.llm_done += 1
                    advance()
                checkpoint(db)
                break
            done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            for future in done:
                app, heads_up = futures[future]
                try:
                    evaluation = future.result()
                    if not swiped(db, app):
                        _apply_evaluation(db, user, app, evaluation, heads_up, resume_vec)
                except Exception as exc:  # noqa: BLE001
                    run.log(f"Evaluation failed for {app.job.company_name}: {exc}", level="error")
                if progress is not None:
                    progress.llm_done += 1
                advance()
            if done:
                checkpoint(db)  # each scored card reaches Swipe Review right away
            elif progress is not None:
                progress.tick()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
