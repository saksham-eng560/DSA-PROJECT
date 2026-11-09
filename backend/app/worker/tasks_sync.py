"""LinkedIn profile sync tasks."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.core.database import session_scope
from app.models.user import User
from app.services.linkedin_sync import sync_linkedin_profile
from app.worker.celery_app import celery_app
from app.worker.dispatch import enqueue, register


@register("linkedin_sync_user")
def linkedin_sync_user(user_id: str) -> dict[str, Any]:
    with session_scope() as db:
        user = db.get(User, uuid.UUID(user_id))
        if user is None:
            return {"status": "missing"}
        result = sync_linkedin_profile(db, user)
        result.pop("diff", None)
        return result


@register("linkedin_sync_all")
def linkedin_sync_all() -> int:
    with session_scope() as db:
        ids = [str(u) for u in db.scalars(
            select(User.id).where(User.is_active.is_(True), User.linkedin_session_cookie.is_not(None))
        ).all()]
    for uid in ids:
        enqueue("linkedin_sync_user", uid)
    return len(ids)


@celery_app.task(name="autoapply.linkedin_sync_user", soft_time_limit=300)
def linkedin_sync_user_task(user_id: str) -> dict[str, Any]:
    return linkedin_sync_user(user_id)


@celery_app.task(name="autoapply.linkedin_sync_all")
def linkedin_sync_all_task() -> int:
    return linkedin_sync_all()
