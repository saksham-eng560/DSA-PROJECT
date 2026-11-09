"""FastAPI dependencies: DB session, authentication, rate limiting."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import get_db
from app.core.security import TokenError, decode_token
from app.models.user import User


def _rate_key(request: Request) -> str:
    token = request.cookies.get(settings.COOKIE_NAME) or request.headers.get("authorization", "")
    return f"{get_remote_address(request)}:{hash(token) if token else ''}"


limiter = Limiter(key_func=_rate_key, default_limits=[settings.RATE_LIMIT_DEFAULT],
                  storage_uri=settings.REDIS_URL if settings.REDIS_URL and not settings.is_sqlite else "memory://",
                  enabled=settings.ENVIRONMENT != "test")

# scope="function": the request's changes are committed before the response goes out. With the default ("request")
# FastAPI commits after sending it, so the dashboard's refetch right after a click could still read the old state.
DB = Annotated[Session, Depends(get_db, scope="function")]


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(settings.COOKIE_NAME)


def _user_from_request(request: Request, db: Session, scopes: tuple[str, ...]) -> User:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(token, expected_scopes=scopes)
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, ValueError, KeyError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc) or "Invalid token") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def get_current_user(request: Request, db: DB) -> User:
    return _user_from_request(request, db, ("access",))


def get_current_user_or_extension(request: Request, db: DB) -> User:
    return _user_from_request(request, db, ("access", "extension"))


CurrentUser = Annotated[User, Depends(get_current_user)]
ExtensionUser = Annotated[User, Depends(get_current_user_or_extension)]


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found") from exc
