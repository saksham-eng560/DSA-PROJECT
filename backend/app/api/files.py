"""Authenticated access to stored files (resume PDFs, screenshots) + inbound webhooks + WebSocket."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import uuid
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import RedirectResponse, Response

from app.api.deps import CurrentUser
from app.config import settings
from app.core.database import SessionLocal
from app.core.security import TokenError, decode_token
from app.core.storage import S3Storage, get_storage, user_prefix
from app.core.websocket import manager
from app.models.user import User
from app.worker.dispatch import enqueue

logger = logging.getLogger(__name__)
router = APIRouter(tags=["files"])


@router.get("/files/{key:path}")
def get_file(key: str, user: CurrentUser) -> Response:
    key = unquote(key)
    if not key.startswith(user_prefix(user.id) + "/") or ".." in key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    storage = get_storage()
    if isinstance(storage, S3Storage):
        url = storage.presigned_url(key, expires=300)
        if url:
            return RedirectResponse(url)
    try:
        data = storage.read(key)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found") from exc
    media = mimetypes.guess_type(key)[0] or "application/octet-stream"
    return Response(data, media_type=media, headers={"Cache-Control": "private, max-age=300"})


@router.post("/webhooks/gmail", status_code=204)
async def gmail_push(request: Request, token: str | None = None) -> Response:
    """Google Pub/Sub push endpoint for Gmail ``users.watch`` notifications."""
    if settings.GMAIL_PUBSUB_VERIFICATION_TOKEN and token != settings.GMAIL_PUBSUB_VERIFICATION_TOKEN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid token")
    try:
        envelope = await request.json()
        data = json.loads(base64.b64decode(envelope["message"]["data"]).decode("utf-8"))
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed Pub/Sub message") from exc
    if data.get("emailAddress"):
        enqueue("handle_gmail_push", data["emailAddress"], str(data.get("historyId") or ""))
    return Response(status_code=204)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = None) -> None:
    """Real-time dashboard updates. Authenticate with ``/auth/ws-token`` (or the session cookie)."""
    raw = token or websocket.cookies.get(settings.COOKIE_NAME)
    try:
        payload = decode_token(raw or "", expected_scopes=("ws", "access"))
        user_id = str(uuid.UUID(payload["sub"]))
    except (TokenError, ValueError, KeyError):
        await websocket.close(code=4401)
        return
    with SessionLocal() as db:
        user = db.get(User, uuid.UUID(user_id))
        if user is None or not user.is_active:
            await websocket.close(code=4401)
            return
    await manager.connect(user_id, websocket)
    try:
        await websocket.send_json({"type": "connected", "data": {"user_id": user_id}})
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"type": "pong", "data": {}})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user_id, websocket)
