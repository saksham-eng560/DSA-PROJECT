"""Application preparation / staging / submission tasks (browser queue)."""

from __future__ import annotations

import logging

from app.core.database import session_scope
from app.services import agent_orchestrator as orch
from app.worker.celery_app import celery_app
from app.worker.dispatch import register

logger = logging.getLogger(__name__)


@register("prepare_application")
def prepare_application(application_id: str) -> None:
    with session_scope() as db:
        orch.prepare_application(db, application_id)


@register("stage_application")
def stage_application(application_id: str) -> None:
    with session_scope() as db:
        orch.stage_application(db, application_id)


@register("submit_application")
def submit_application(application_id: str) -> None:
    with session_scope() as db:
        orch.submit_application(db, application_id)


@celery_app.task(name="autoapply.prepare_application", soft_time_limit=900)
def prepare_application_task(application_id: str) -> None:
    prepare_application(application_id)


@celery_app.task(name="autoapply.stage_application", soft_time_limit=600)
def stage_application_task(application_id: str) -> None:
    stage_application(application_id)


@celery_app.task(name="autoapply.submit_application", soft_time_limit=600)
def submit_application_task(application_id: str) -> None:
    submit_application(application_id)
