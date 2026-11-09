"""Live progress of a job scan, for the dashboard's progress bar.

A scan has four phases: searching every job source (in parallel), saving the postings, scoring
them against your resume, and wrapping up. Scraper threads report into a :class:`ScanProgress`;
only the scan's own thread writes it to ``agent_runs.progress`` (throttled) and pushes a
``scan_progress`` WebSocket event, so the bar moves in real time without polling.

``percent`` is an estimate built from the phases (searching 2-55 %, saving 55-62 %, scoring
62-97 %, wrapping up 97-100 %). A source that reports how many boards / pages it has is exact;
one that doesn't creeps forward on a time curve until it finishes.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import checkpoint
from app.models.agent_run import AgentRun
from app.services.notifier import push_update

PHASES: dict[str, tuple[float, float]] = {
    "queued": (0, 2),
    "discovering": (2, 55),
    "saving": (55, 62),
    "scoring": (62, 97),
    "finishing": (97, 100),
}
FLUSH_INTERVAL = 0.8  # seconds between progress writes


class ScanCancelled(Exception):
    """You pressed Stop on the scan."""


class ScanProgress:
    def __init__(self, db: Session, run: AgentRun, sources: list[str]) -> None:
        self.db = db
        self.run = run
        self._lock = threading.Lock()
        self.phase = "discovering"
        self.message = "Searching job sources"
        self.sources: dict[str, dict[str, Any]] = {
            name: {"name": name, "status": "pending", "found": 0, "done": 0, "total": 0} for name in sources
        }
        self._started: dict[str, float] = {}
        self._stepped: dict[str, float] = {}  # when each source last completed a board / page
        self.found = 0
        self.new = 0
        self.saved = 0
        self.to_save = 0
        self.scored = 0
        self.to_score = 0
        self._scoring_t0: float | None = None
        # The AI part of scoring: these take seconds to minutes each, the rest are instant.
        self.llm_total = 0
        self.llm_done = 0
        self.llm_concurrency = 1
        self.llm_deadline: float | None = None  # monotonic time the scan stops waiting for the AI
        self._last_flush = 0.0
        self.cancelled = False

    # ------------------------------------------------------------------ scraper threads
    def source_started(self, name: str) -> None:
        with self._lock:
            self._started[name] = time.monotonic()
            self.sources[name]["status"] = "running"

    def source_step(self, name: str, done: int, total: int) -> None:
        with self._lock:
            if done != self.sources[name]["done"] or not self.sources[name]["total"]:
                self._stepped[name] = time.monotonic()
            self.sources[name]["done"] = done
            self.sources[name]["total"] = total

    def source_finished(self, name: str, found: int, status: str = "done", error: str | None = None) -> None:
        with self._lock:
            src = self.sources[name]
            src.update(status=status, found=found)
            if src["total"]:
                src["done"] = src["total"]
            if error:
                src["error"] = error[:200]

    # ------------------------------------------------------------------ scan thread
    def set_phase(self, phase: str, message: str) -> None:
        with self._lock:
            self.phase = phase
            self.message = message
            if phase == "scoring" and self._scoring_t0 is None:
                self._scoring_t0 = time.monotonic()
        self.flush(force=True)

    def _source_fraction(self, name: str, src: dict[str, Any], now: float) -> float:
        if src["status"] in ("done", "failed", "timeout", "skipped"):
            return 1.0
        if src["status"] != "running":
            return 0.0
        if src["total"]:
            step = 1 / src["total"]
            waiting = now - self._stepped.get(name, now)  # creep toward the next step so a slow page never looks frozen
            within = step * 0.8 * (1 - math.exp(-waiting / 20))
            return 0.05 + 0.9 * min(1.0, src["done"] / src["total"] + within)
        elapsed = now - self._started.get(name, now)
        return min(0.85, 1 - math.exp(-elapsed / 40))  # no step count: creep forward, slower and slower

    def percent(self) -> int:
        lo, hi = PHASES.get(self.phase, (0, 100))
        now = time.monotonic()
        if self.phase == "discovering":
            fractions = [self._source_fraction(n, s, now) for n, s in self.sources.items()]
            frac = sum(fractions) / len(fractions) if fractions else 1.0
        elif self.phase == "saving":
            frac = self.saved / self.to_save if self.to_save else 1.0
        elif self.phase == "scoring":
            frac = self.scored / self.to_score if self.to_score else 1.0
        else:
            frac = 0.0
        return int(lo + (hi - lo) * min(1.0, frac))

    def eta_seconds(self) -> int | None:
        if self.phase != "scoring" or self._scoring_t0 is None or not self.to_score:
            return None
        now = time.monotonic()
        waiting_for_ai = self.llm_total - self.llm_done
        if waiting_for_ai > 0:
            # Only the AI calls take time: estimate from them, never past the point the scan stops waiting.
            cap = max(1, int(self.llm_deadline - now)) if self.llm_deadline is not None else None
            if self.llm_done == 0:
                return cap  # no AI answer yet: the honest bound is the time budget
            per_call = (now - self._scoring_t0) / self.llm_done
            estimate = int(per_call * math.ceil(waiting_for_ai / max(1, self.llm_concurrency))) + 2
            return min(estimate, cap) if cap is not None else estimate
        if self.scored < 2:
            return None
        per_job = (now - self._scoring_t0) / self.scored
        return max(1, int(per_job * (self.to_score - self.scored))) + 2

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "phase": self.phase,
                "percent": self.percent(),
                "message": self.message,
                "sources": [dict(s) for s in self.sources.values()],
                "found": self.found,
                "new": self.new,
                "scored": self.scored,
                "to_score": self.to_score,
                "eta_seconds": self.eta_seconds(),
                "llm_total": self.llm_total,
                "llm_done": self.llm_done,
                "updated_at": datetime.now(UTC).isoformat(),
            }

    def flush(self, force: bool = False) -> None:
        """Save + push the current progress (at most every FLUSH_INTERVAL seconds unless forced).

        Commits the scan's session: never call it inside ``begin_nested()``.
        """
        now = time.monotonic()
        if not force and now - self._last_flush < FLUSH_INTERVAL:
            return
        self._last_flush = now
        snap = self.snapshot()
        self.run.progress = snap
        checkpoint(self.db)
        # Stop pressed? The cancel endpoint only changes the status column, so read it fresh.
        status = self.db.scalar(select(AgentRun.status).where(AgentRun.id == self.run.id))
        if status == "cancelled":
            self.cancelled = True
        push_update(str(self.run.user_id), "scan_progress", {"run_id": str(self.run.id), "progress": snap})

    def tick(self) -> None:
        """Flush if due; raise :class:`ScanCancelled` when you've pressed Stop."""
        self.flush()
        if self.cancelled:
            raise ScanCancelled

    def finish(self, status: str, summary: str) -> None:
        with self._lock:
            self.phase = "done" if status == "completed" else status
            self.message = summary
        snap = self.snapshot()
        snap["percent"] = 100 if status == "completed" else snap["percent"]
        self.run.progress = snap
