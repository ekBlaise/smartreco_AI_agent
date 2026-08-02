"""Password hashing and the signed session cookie."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

SESSION_COOKIE = "smartreco_session"
_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_session_token(user_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=settings.session_ttl_hours),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


def decode_session_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None
