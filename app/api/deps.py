"""Shared FastAPI dependencies: session lookup and role enforcement."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Role, User
from app.security import SESSION_COOKIE, decode_session_token

ANON_COOKIE = "smartreco_anon"


class RedirectToLogin(Exception):
    """Raised by page dependencies so the handler can 302 instead of 401."""

    def __init__(self, next_url: str = "/") -> None:
        self.next_url = next_url


def current_user_optional(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    payload = decode_session_token(token)
    if not payload:
        return None
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


def require_user(user: User | None = Depends(current_user_optional)) -> User:
    """For JSON APIs — 401 rather than a redirect."""
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != Role.ADMIN.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


def require_user_page(request: Request, user: User | None = Depends(current_user_optional)) -> User:
    """For HTML pages — bounce to /login preserving where they were headed."""
    if user is None:
        raise RedirectToLogin(request.url.path)
    return user


def require_admin_page(user: User = Depends(require_user_page)) -> User:
    if user.role != Role.ADMIN.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


def get_session_id(request: Request) -> str:
    """A stable per-browser id so anonymous behaviour is still attributable.

    Set by :class:`app.api.middleware.AnonSessionMiddleware`. Events from a
    signed-out visitor are keyed by this; once they log in the user_id takes
    over and the two can be stitched together.
    """
    return getattr(request.state, "session_id", "") or request.cookies.get(ANON_COOKIE, "")


def login_redirect(next_url: str = "/") -> RedirectResponse:
    target = f"/login?next={next_url}" if next_url and next_url != "/" else "/login"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
