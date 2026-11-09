"""The agent "brain": discovery -> matching -> preparation -> staging -> (approval) -> submission.

This module is the agent's public interface for the API and the worker tasks. Each stage lives in its own
module of ``app.services.agent`` (see that package for the map); import from there to test or patch a stage.

Behavioural rules (PLAN.md §8):
 1. Never process a job that already has an application for this user.            agent.jobs
 2. Never apply to the same company + role twice.                                 agent.jobs, agent.scoring
 3. Respect max_applications_per_day and per-platform limits (rate_limiter).      agent.scan, agent.submission
 4. Unexpected form fields -> flag for manual review.                             agent.submission
 5. Screenshot at every critical step (staged form, confirmation, failures).      agent.submission
 6. Every run is logged to ``agent_runs``.                                        agent.runs
 7. Low-confidence answers -> needs_user_review.                                  agent.submission
 8. Prioritise by match_score DESC, deadline ASC.                                 agent.scan
 9. Salary questions use the bottom of the user's range (question_answerer).
10. A failed form is retried once before alerting the user.                       agent.submission
And DIRECTIVE 2: nothing is ever submitted without explicit user approval.

Swipe Review (``review_mode="swipe"``, the default): scans never skip a job for a low score.
Every job that passes your hard filters waits in Swipe Review; keeping one is your approval to
prepare it and, with ``auto_submit_kept``, to submit it once the form is filled with every required
answer. Anything the agent is unsure about still stops in "Needs approval".
"""

from __future__ import annotations

from app.services.agent.discovery import discover_jobs, scan_platforms
from app.services.agent.documents import RESUME_STRATEGIES, render_tailored_pdf
from app.services.agent.internshala import (
    INTERNSHALA_FILLING,
    INTERNSHALA_MISSING,
    bot_apply,
    internshala_missing,
    internshala_submitted_today,
    is_internshala_job,
    restage_internshala_waiting,
)
from app.services.agent.jobs import backfill_company_checks, enrich_job, ensure_application, get_master_resume, resume_embedding, upsert_job
from app.services.agent.preparation import prepare_application
from app.services.agent.review import (
    keep_application,
    skip_application,
    skip_ineligible_waiting,
    skip_suspicious_waiting,
    undo_review,
    waiting_count,
)
from app.services.agent.runs import RunLog, close_stale_scans, resume_interrupted_work
from app.services.agent.scan import import_job_url, run_scan
from app.services.agent.scoring import evaluate_application
from app.services.agent.submission import (
    approve_application,
    build_packet,
    direct_submit_blocker,
    mark_self_applied,
    ready_or_submit,
    stage_application,
    submit_application,
)

__all__ = [
    "INTERNSHALA_FILLING",
    "INTERNSHALA_MISSING",
    "RESUME_STRATEGIES",
    "RunLog",
    "approve_application",
    "backfill_company_checks",
    "bot_apply",
    "build_packet",
    "close_stale_scans",
    "direct_submit_blocker",
    "discover_jobs",
    "enrich_job",
    "ensure_application",
    "evaluate_application",
    "get_master_resume",
    "import_job_url",
    "internshala_missing",
    "internshala_submitted_today",
    "is_internshala_job",
    "keep_application",
    "mark_self_applied",
    "prepare_application",
    "ready_or_submit",
    "render_tailored_pdf",
    "restage_internshala_waiting",
    "resume_embedding",
    "resume_interrupted_work",
    "run_scan",
    "scan_platforms",
    "skip_application",
    "skip_ineligible_waiting",
    "skip_suspicious_waiting",
    "stage_application",
    "submit_application",
    "undo_review",
    "upsert_job",
    "waiting_count",
]
