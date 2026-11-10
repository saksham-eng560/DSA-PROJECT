"""Career copilot (chatbot) endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, parse_uuid
from app.api.serializers import iso
from app.config import settings
from app.dsa.rate_limit import SlidingWindowLimiter
from app.models.chat import ChatMessage, ChatSession
from app.services import copilot
from app.services.llm import active_model, get_llm

router = APIRouter(prefix="/copilot", tags=["copilot"])

# 20 messages a minute per user, counted in a sliding window (a deque of timestamps per user).
chat_limiter = SlidingWindowLimiter(limit=20, window_seconds=60)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=copilot.MAX_MESSAGE_CHARS)
    session_id: str | None = None


def message_out(message: ChatMessage) -> dict[str, Any]:
    meta = message.meta or {}
    return {"id": str(message.id), "role": message.role, "content": message.content, "created_at": iso(message.created_at),
            "trace": meta.get("trace"), "cards": meta.get("cards") or [], "follow_ups": meta.get("follow_ups") or []}


def session_out(session: ChatSession, messages: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": str(session.id), "title": session.title, "created_at": iso(session.created_at),
                           "updated_at": iso(session.updated_at)}
    if messages is not None:
        out["messages"] = messages
    return out


def _owned(db: DB, user: CurrentUser, session_id: str) -> ChatSession:
    session = db.get(ChatSession, parse_uuid(session_id))
    if session is None or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return session


@router.get("/status")
def copilot_status(user: CurrentUser) -> dict:
    llm = get_llm()
    return {"llm_available": llm.available, "provider": llm.primary, "model": active_model(llm),
            "mode": "llm" if llm.available else "rules",
            "memory_messages": copilot.MEMORY_MESSAGES, "intents": sorted(copilot.TOOLS),
            "starters": ["How am I doing so far?", "Which jobs should I apply to first?", "What skills am I missing?",
                         "How does the trie work here?", "Any interview clashes this week?"]}


@router.post("/chat")
def chat(body: ChatIn, user: CurrentUser, db: DB) -> dict:
    if not body.message.strip():
        raise HTTPException(422, "Message is empty")
    if settings.ENVIRONMENT != "test" and not chat_limiter.allow(str(user.id)):
        retry = int(chat_limiter.retry_after(str(user.id))) + 1
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Too many messages. Try again in {retry} seconds.",
                            headers={"Retry-After": str(retry)})
    session_id = parse_uuid(body.session_id) if body.session_id else None
    result = copilot.reply(db, user, body.message, session_id)
    return {"session": session_out(result["session"]), "user_message": message_out(result["user_message"]),
            "message": message_out(result["assistant_message"])}


@router.get("/sessions")
def list_sessions(user: CurrentUser, db: DB) -> dict:
    rows = db.execute(
        select(ChatSession, func.count(ChatMessage.id))
        .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.user_id == user.id)
        .group_by(ChatSession.id).order_by(ChatSession.updated_at.desc()).limit(50)).all()
    return {"items": [session_out(session, count) for session, count in rows]}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, user: CurrentUser, db: DB) -> dict:
    session = _owned(db, user, session_id)
    return {"session": session_out(session), "messages": [message_out(m) for m in session.messages]}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, user: CurrentUser, db: DB) -> dict:
    db.delete(_owned(db, user, session_id))
    return {"deleted": True}
