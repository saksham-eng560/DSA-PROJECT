"""Analytics & notifications endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select, update

from app.api.deps import DB, CurrentUser, parse_uuid
from app.api.serializers import notification_out
from app.models.user import Notification
from app.services.analytics import compute_overview

router = APIRouter(tags=["analytics"])


@router.get("/analytics/overview")
def overview(user: CurrentUser, db: DB, days: int | None = Query(default=None, ge=1, le=3650)) -> dict:
    return compute_overview(db, user, days)


@router.get("/notifications")
def list_notifications(user: CurrentUser, db: DB, unread: bool | None = None,
                       limit: int = Query(default=30, ge=1, le=200)) -> dict:
    query = select(Notification).where(Notification.user_id == user.id)
    if unread:
        query = query.where(Notification.is_read.is_(False))
    items = db.scalars(query.order_by(Notification.created_at.desc()).limit(limit)).all()
    unread_count = db.scalar(select(func.count()).select_from(Notification)
                             .where(Notification.user_id == user.id, Notification.is_read.is_(False)))
    return {"items": [notification_out(n) for n in items], "unread": unread_count}


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: str, user: CurrentUser, db: DB) -> dict:
    n = db.get(Notification, parse_uuid(notification_id))
    if n is None or n.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    n.is_read = True
    return notification_out(n)


@router.post("/notifications/read-all")
def mark_all_read(user: CurrentUser, db: DB) -> dict:
    db.execute(update(Notification).where(Notification.user_id == user.id, Notification.is_read.is_(False))
               .values(is_read=True))
    return {"ok": True}
