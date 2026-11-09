"""Google OAuth2 (Gmail + Calendar scopes) and credential management."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import create_token, decode_token
from app.models.user import User

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint URL, not a secret
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

LOGIN_SCOPES = ["openid", "email", "profile"]
INTEGRATION_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]


class GoogleNotConfigured(Exception):
    pass


class GoogleAuthError(Exception):
    pass


def build_auth_url(mode: str, user_id: str | None = None, redirect_after: str | None = None) -> str:
    """mode='login' (sign in/up with Google) or 'connect' (grant Gmail/Calendar to an existing user)."""
    if not settings.google_configured:
        raise GoogleNotConfigured("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set")
    state = create_token(
        user_id or "anonymous",
        scope="oauth_state",
        expires_delta=timedelta(minutes=15),
        extra={"mode": mode, "next": redirect_after or "/dashboard/settings"},
    )
    scopes = LOGIN_SCOPES + (INTEGRATION_SCOPES if mode == "connect" else [])
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent" if mode == "connect" else "select_account",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def parse_state(state: str) -> dict[str, Any]:
    return decode_token(state, expected_scopes=("oauth_state",))


def exchange_code(code: str) -> dict[str, Any]:
    response = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise GoogleAuthError(f"Token exchange failed: {response.text[:300]}")
    return response.json()


def fetch_userinfo(access_token: str) -> dict[str, Any]:
    response = httpx.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=20)
    if response.status_code != 200:
        raise GoogleAuthError("Could not fetch Google profile")
    return response.json()


def store_tokens(user: User, tokens: dict[str, Any], email: str | None = None) -> None:
    user.google_access_token = tokens.get("access_token")
    if tokens.get("refresh_token"):
        user.google_refresh_token = tokens["refresh_token"]
    if tokens.get("expires_in"):
        user.google_token_expiry = datetime.now(UTC) + timedelta(seconds=int(tokens["expires_in"]) - 60)
    granted = (tokens.get("scope") or "").split()
    if granted:
        user.google_scopes = sorted(set(user.google_scopes or []) | set(granted))
    if email:
        user.google_email = email
    consents = dict(user.consents or {})
    if any("gmail" in s for s in granted):
        consents["gmail"] = datetime.now(UTC).isoformat()
    if any("calendar" in s for s in granted):
        consents["calendar"] = datetime.now(UTC).isoformat()
    user.consents = consents


def has_scope(user: User, fragment: str) -> bool:
    return any(fragment in s for s in (user.google_scopes or []))


def get_credentials(db: Session, user: User) -> Any:
    """Return refreshed ``google.oauth2.credentials.Credentials`` for the user."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not settings.google_configured:
        raise GoogleNotConfigured("Google OAuth is not configured")
    if not user.google_refresh_token and not user.google_access_token:
        raise GoogleAuthError("Google account not connected")
    expiry = user.google_token_expiry.replace(tzinfo=None) if user.google_token_expiry else None
    creds = Credentials(
        token=user.google_access_token,
        refresh_token=user.google_refresh_token,
        token_uri=TOKEN_URL,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=user.google_scopes or INTEGRATION_SCOPES,
        expiry=expiry,
    )
    if not creds.valid and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            raise GoogleAuthError(f"Google token refresh failed: {exc}") from exc
        user.google_access_token = creds.token
        if creds.expiry:
            user.google_token_expiry = creds.expiry.replace(tzinfo=UTC)
        db.flush()
    return creds


def revoke(user: User) -> None:
    token = user.google_refresh_token or user.google_access_token
    if token:
        try:
            httpx.post(REVOKE_URL, params={"token": token}, timeout=10)
        except httpx.HTTPError:
            logger.warning("Google token revoke failed for user %s", user.id)
    user.google_access_token = None
    user.google_refresh_token = None
    user.google_token_expiry = None
    user.google_scopes = None
    user.gmail_history_id = None
    user.gmail_watch_expiration = None


def build_service(db: Session, user: User, api: str, version: str) -> Any:
    from googleapiclient.discovery import build

    return build(api, version, credentials=get_credentials(db, user), cache_discovery=False)
