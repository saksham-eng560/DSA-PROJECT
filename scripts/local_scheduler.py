#!/usr/bin/env python3
"""Celery-Beat stand-in for local mode (no Redis): runs the periodic agent jobs in-process.

    python scripts/local_scheduler.py        # started for you by ./start.sh

Scheduled scans, e-mail polling, interview reminders and clean-ups run here; the tasks they
enqueue (preparing kept jobs, submitting) run in this process's background threads.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "backend" if (_ROOT / "backend" / "app").is_dir() else _ROOT))

from app.config import settings
from app.worker import dispatch

logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s [scheduler] %(levelname)s %(message)s")
log = logging.getLogger("scheduler")

# (task name, interval in seconds, run on start-up?)
SCHEDULE: list[tuple[str, int, bool]] = [
    ("scan_due_users", 60 * 60, True),
    ("check_all_emails", max(60, settings.EMAIL_POLL_MINUTES * 60), False),
    ("send_interview_reminders", 10 * 60, False),
    ("renew_gmail_watches", 24 * 3600, False),
    ("linkedin_sync_all", 24 * 3600, False),
    ("expire_stale_jobs", 24 * 3600, False),
    ("retention_cleanup", 24 * 3600, False),
    ("weekly_summary", 7 * 24 * 3600, False),
    ("progress_digest", 60 * 60, False),  # sends at ~20:00 in your time zone
]

_running = True


def _stop(*_: object) -> None:
    global _running
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    dispatch._load_registry()
    now = time.monotonic()
    next_run = {name: (now + 20 if on_start else now + interval) for name, interval, on_start in SCHEDULE}
    log.info("Local scheduler started: %s", ", ".join(name for name, _, _ in SCHEDULE))
    while _running:
        now = time.monotonic()
        for name, interval, _ in SCHEDULE:
            if now >= next_run[name]:
                next_run[name] = now + interval
                try:
                    result = dispatch._registry[name]()
                    log.info("%s -> %s", name, result)
                except Exception:  # keep the loop alive whatever a job does
                    log.exception("%s failed", name)
        time.sleep(5)
    log.info("Local scheduler stopped")


if __name__ == "__main__":
    main()
