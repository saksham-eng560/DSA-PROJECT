#!/usr/bin/env python3
"""Clear the job postings you haven't acted on, so the next scan starts from a clean deck.

    backend/.venv/bin/python scripts/clear_jobs.py            # show what would be cleared
    backend/.venv/bin/python scripts/clear_jobs.py --yes      # clear it
    ./start.sh --clear-jobs                                   # clear it, then start the app

Cleared: postings waiting in Swipe Review, ones the agent or you skipped, and ones that only failed to
prepare. Kept: everything you kept or applied to (preparing, waiting for approval, applied, interviews,
offers, rejections...), with its job posting, history, e-mails and interviews. Your account, resume,
settings and saved answers are not touched. Uses the same local database as ./start.sh
(LOCAL_DATABASE_URL, else backend/data/autoapply.db); set DATABASE_URL to clear another one.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend" if (ROOT / "backend" / "app").is_dir() else ROOT
sys.path.insert(0, str(BACKEND))
if not os.environ.get("DATABASE_URL"):  # what ./start.sh uses in local mode
    os.environ["DATABASE_URL"] = os.environ.get("LOCAL_DATABASE_URL") or f"sqlite:///{ROOT / 'backend' / 'data' / 'autoapply.db'}"

from sqlalchemy import delete, func, select

from app.core.database import session_scope
from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.models.job import Job
from app.models.resume import Resume
from app.models.user import User

# Nothing was sent for these, and you didn't keep them.
CLEARABLE = (ApplicationStatus.DISCOVERED, ApplicationStatus.MATCHED, ApplicationStatus.SKIPPED, ApplicationStatus.FAILED)


def clear(email: str | None, apply: bool) -> int:
    with session_scope() as db:
        users = select(User.id)
        if email:
            users = users.where(func.lower(User.email) == email.lower())
        user_ids = list(db.scalars(users))
        if email and not user_ids:
            print(f"No account {email} in this database.")
            return 1
        mine = (Application.user_id.in_(user_ids), Application.status.in_(CLEARABLE),
                Application.review_decision.is_distinct_from("keep"))  # a kept card stays even before it's prepared
        apps = db.scalar(select(func.count()).select_from(Application).where(*mine)) or 0
        kept = db.scalar(select(func.count()).select_from(Application)
                         .where(Application.user_id.in_(user_ids)).where(~Application.id.in_(
                             select(Application.id).where(*mine)))) or 0
        print(f"{apps} job posting{'s' if apps != 1 else ''} to clear (waiting in Swipe Review, skipped or failed); "
              f"{kept} you kept or applied to stay.")
        if not apply:
            print("Nothing changed. Run again with --yes to clear them.")
            return 0
        db.execute(delete(Application).where(*mine).execution_options(synchronize_session=False))
        # Postings no account still has an application for (shared postings another account kept stay).
        orphans = select(Job.id).where(~Job.id.in_(select(Application.job_id)))
        db.execute(Resume.__table__.update().where(Resume.tailored_for_job_id.in_(orphans)).values(tailored_for_job_id=None))
        jobs = db.execute(delete(Job).where(Job.id.in_(orphans)).execution_options(synchronize_session=False)).rowcount
        print(f"Cleared {apps} posting{'s' if apps != 1 else ''} ({jobs or 0} job record{'s' if jobs != 1 else ''} removed). "
              "Run a scan to fill your deck with what your current settings look for.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="really clear (otherwise only show the counts)")
    parser.add_argument("--email", help="only this account (default: every account in this database)")
    args = parser.parse_args()
    sys.exit(clear(args.email, args.yes))


if __name__ == "__main__":
    main()
