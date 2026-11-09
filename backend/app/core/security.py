"""Password hashing, JWT tokens and AES-256-GCM field encryption."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import settings

JWT_ALGORITHM = "HS256"
_ENC_PREFIX = "v1:"


# --------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    # bcrypt only considers the first 72 bytes; pre-hash to support long passphrases safely.
    digest = base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())
    return bcrypt.hashpw(digest, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    digest = base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())
    try:
        return bcrypt.checkpw(digest, hashed.encode("utf-8"))
    except ValueError:
        return False


# --------------------------------------------------------------------------- JWT
class TokenError(Exception):
    pass


def create_token(
    subject: str,
    scope: str = "access",
    expires_delta: timedelta | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))).timestamp()),
        "jti": secrets.token_hex(8),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str, expected_scopes: tuple[str, ...] = ("access",)) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid token") from exc
    if payload.get("scope") not in expected_scopes:
        raise TokenError("Token scope not allowed here")
    return payload


# --------------------------------------------------------------------------- encryption
def _load_key() -> bytes:
    if settings.ENCRYPTION_KEY:
        raw = settings.ENCRYPTION_KEY.strip()
        padded = raw + "=" * (-len(raw) % 4)
        try:
            key = base64.urlsafe_b64decode(padded)
        except Exception:  # noqa: BLE001
            key = b""
        if len(key) == 32:
            return key
        # Allow arbitrary passphrases by deriving a key from them.
        return hashlib.sha256(raw.encode("utf-8")).digest()
    # Development fallback: derive from SECRET_KEY so data stays readable across restarts.
    return hashlib.sha256(("enc:" + settings.SECRET_KEY).encode("utf-8")).digest()


_KEY = _load_key()


def encrypt_str(plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    nonce = os.urandom(12)
    ciphertext = AESGCM(_KEY).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _ENC_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_str(token: str | None) -> str | None:
    if token is None:
        return None
    if not token.startswith(_ENC_PREFIX):
        return token  # legacy / plaintext value
    blob = base64.urlsafe_b64decode(token[len(_ENC_PREFIX):].encode("ascii"))
    nonce, ciphertext = blob[:12], blob[12:]
    return AESGCM(_KEY).decrypt(nonce, ciphertext, None).decode("utf-8")


class EncryptedText(TypeDecorator):
    """Text column transparently encrypted with AES-256-GCM at rest."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        return encrypt_str(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        try:
            return decrypt_str(value)
        except Exception:  # noqa: BLE001 - wrong key / corrupted data should not crash reads
            return None


class EncryptedJSON(TypeDecorator):
    """JSON document stored encrypted (AES-256-GCM) as text."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return encrypt_str(json.dumps(value, sort_keys=True))

    def process_result_value(self, value: str | None, dialect: Any) -> Any:
        if value is None:
            return None
        try:
            decrypted = decrypt_str(value)
            return json.loads(decrypted) if decrypted else None
        except Exception:  # noqa: BLE001
            return None


def generate_encryption_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
