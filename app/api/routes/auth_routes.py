"""Email/password auth with a signed httpOnly session cookie."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import LoginIn, RegisterIn
from app.api.templating import templates
from app.config import settings
from app.database import get_db
from app.models import Role, User
from app.security import SESSION_COOKIE, create_session_token, hash_password, verify_password

router = APIRouter(tags=["auth"])


def _set_session(response, user: User) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.id, user.role),
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.public_base_url.startswith("https"),
    )


def _safe_next(next_url: str | None) -> str:
    """Only ever redirect to a path on this site."""
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return "/dashboard"
    return next_url


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/dashboard", error: str = ""):
    return templates.TemplateResponse(
        request, "login.html", {"next": _safe_next(next), "error": error, "user": None}
    )


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard"),
    db: Session = Depends(get_db),
):
    try:
        payload = LoginIn(email=email, password=password)
    except ValidationError:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": _safe_next(next), "error": "Enter a valid email address.", "user": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": _safe_next(next), "error": "Incorrect email or password.", "user": None},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    response = RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    _set_session(response, user)
    return response


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, error: str = ""):
    return templates.TemplateResponse(
        request, "register.html", {"error": error, "user": None}
    )


@router.post("/register")
def register(
    request: Request,
    email: str = Form(...),
    full_name: str = Form(""),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        payload = RegisterIn(email=email, full_name=full_name, password=password)
    except ValidationError:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "error": "Enter a valid email and a password of at least 8 characters.",
                "user": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    normalized = payload.email.lower()
    if db.scalar(select(User).where(User.email == normalized)):
        return templates.TemplateResponse(
            request,
            "register.html",
            {"error": "That email is already registered.", "user": None},
            status_code=status.HTTP_409_CONFLICT,
        )

    user = User(
        email=normalized,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role=Role.USER.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_session(response, user)
    return response


@router.post("/logout")
@router.get("/logout")
def logout():
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response
