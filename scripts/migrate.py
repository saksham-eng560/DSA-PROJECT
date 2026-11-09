#!/usr/bin/env python3
"""Database migrations & maintenance.

    python scripts/migrate.py                 # alembic upgrade head (PostgreSQL) / create tables (SQLite)
    python scripts/migrate.py --reembed       # recompute all embeddings (after changing EMBEDDING_PROVIDER)
    python scripts/migrate.py --check         # fail if models and migrations have drifted
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# Repo checkout: <root>/backend/app ; Docker image: /app/app
BACKEND = _ROOT / "backend" if (_ROOT / "backend" / "app").is_dir() else _ROOT
sys.path.insert(0, str(BACKEND))


MIGRATION_LOCK_ID = 7_214_553_901  # arbitrary constant shared by every replica


def upgrade() -> None:
    from sqlalchemy import text

    from app.config import settings
    from app.core.database import create_all, engine, wait_for_db

    wait_for_db()
    if settings.is_sqlite:
        create_all()
        print("SQLite: tables created")
        return
    # Replicas starting together (API scale-out, rolling deploys) take turns; later ones find nothing to do.
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        try:
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, check=True)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})


def check() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "check"], cwd=BACKEND, check=True)


def reembed() -> None:
    from sqlalchemy import select

    from app.core.database import session_scope
    from app.models.job import Job
    from app.models.resume import Resume
    from app.schemas.resume_content import ResumeContent
    from app.services.embeddings import embed_texts
    from app.services.job_matcher import job_text

    with session_scope() as db:
        jobs = db.scalars(select(Job)).all()
        for start in range(0, len(jobs), 64):
            batch = jobs[start : start + 64]
            for job, vec in zip(batch, embed_texts([job_text(j)[:8000] for j in batch]), strict=True):
                job.description_embedding = vec
        resumes = db.scalars(select(Resume)).all()
        for resume in resumes:
            rc = ResumeContent.model_validate(resume.parsed_content)
            resume.skills_embedding = embed_texts([f"{rc.skills_text()}\n{rc.full_text()}"])[0]
        print(f"Re-embedded {len(jobs)} jobs and {len(resumes)} resumes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reembed", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
    elif args.reembed:
        reembed()
    else:
        upgrade()


if __name__ == "__main__":
    main()
