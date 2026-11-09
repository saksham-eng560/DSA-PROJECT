from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.security import (
    TokenError,
    create_token,
    decode_token,
    decrypt_str,
    encrypt_str,
    hash_password,
    verify_password,
)
from app.models.user import User


def test_password_hashing_roundtrip() -> None:
    hashed = hash_password("correct horse battery staple" * 5)  # > 72 bytes still works
    assert verify_password("correct horse battery staple" * 5, hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("anything", None)


def test_jwt_scopes_and_expiry() -> None:
    token = create_token("user-1")
    assert decode_token(token)["sub"] == "user-1"
    ext = create_token("user-1", scope="extension")
    with pytest.raises(TokenError):
        decode_token(ext)  # extension tokens can't be used as full access tokens
    assert decode_token(ext, expected_scopes=("access", "extension"))["scope"] == "extension"
    expired = create_token("user-1", expires_delta=timedelta(seconds=-5))
    with pytest.raises(TokenError, match="expired"):
        decode_token(expired)
    with pytest.raises(TokenError):
        decode_token("not-a-token")


def test_aes_gcm_encryption_is_randomized() -> None:
    a, b = encrypt_str("secret"), encrypt_str("secret")
    assert a != b and a.startswith("v1:")
    assert decrypt_str(a) == decrypt_str(b) == "secret"
    assert decrypt_str("legacy-plaintext") == "legacy-plaintext"
    assert encrypt_str(None) is None


def test_tokens_encrypted_at_rest(db) -> None:
    user = User(email="x@example.com", full_name="X", google_refresh_token="refresh-123",
                linkedin_session_cookie="li-cookie", ats_credentials={"workday_password": "pw"})
    db.add(user)
    db.commit()
    raw = db.execute(text("SELECT google_refresh_token, linkedin_session_cookie, ats_credentials FROM users")).one()
    assert all(value.startswith("v1:") for value in raw)
    assert "refresh-123" not in raw[0]
    db.expire_all()
    loaded = db.get(User, user.id)
    assert loaded.google_refresh_token == "refresh-123"
    assert loaded.ats_credentials == {"workday_password": "pw"}
