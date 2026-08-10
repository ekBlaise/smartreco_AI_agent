"""Cart: how a visitor commits to a course before they have an account.

Keyed by session, so browsing anonymously and adding to a cart works exactly as
it would on a real marketplace — no sign-in wall in front of the primary action.
Signing in is only required at checkout, and because the session cookie survives
login the cart is still there afterwards.

Checkout creates enrolments. There is deliberately no payment step: this is a
demo marketplace and it never asks for card details.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select, update
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user_optional, get_session_id
from app.security import SESSION_COOKIE, create_session_token
from app.config import settings
from app.database import get_db
from app.ingest import buffer
from app.models import CartItem, Enrollment, Event, Product, Recommendation, User
from app.service import owns_enrollment

logger = logging.getLogger(__name__)
router = APIRouter(tags=["cart"])


def _set_session(response, user: User) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.id, user.role),
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.public_base_url.startswith("https"),
    )


def cart_items(db: Session, session_id: str) -> list[CartItem]:
    if not session_id:
        return []
    return list(
        db.scalars(
            select(CartItem)
            .where(CartItem.session_id == session_id)
            .order_by(CartItem.created_at.desc())
        )
    )


def cart_product_ids(db: Session, session_id: str) -> set[int]:
    if not session_id:
        return set()
    return set(
        db.scalars(select(CartItem.product_id).where(CartItem.session_id == session_id))
    )


def _summary(db: Session, session_id: str) -> dict:
    items = cart_items(db, session_id)
    return {
        "count": len(items),
        "total": round(sum(float(i.product.price or 0) for i in items if i.product), 2),
        "product_ids": [i.product_id for i in items],
    }


@router.post("/api/cart/{product_id}")
def add_to_cart(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
) -> dict:
    """Add a course. Works signed out — that is the point."""
    product = db.get(Product, product_id)
    if product is None or not product.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found")

    session_id = get_session_id(request)
    if not session_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No session")

    db.add(
        CartItem(
            session_id=session_id,
            user_id=user.id if user else None,
            product_id=product_id,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # already there; adding twice is not an error

    # Putting something in a basket is a strong intent signal, so it goes
    # through the same buffered path as every other tracked action.
    buffer.push_events(
        [
            {
                "user_id": user.id if user else None,
                "session_id": session_id,
                "type": "enroll_intent",
                "product_id": product_id,
                "query": None,
                "path": f"/course/{product.slug}",
                "dwell_ms": 0,
                "meta": {"category": product.category, "surface": "add_to_cart"},
                "occurred_at": None,
            }
        ]
    )
    return {"in_cart": True, **_summary(db, session_id)}


@router.delete("/api/cart/{product_id}")
def remove_from_cart(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    session_id = get_session_id(request)
    db.execute(
        delete(CartItem).where(
            CartItem.session_id == session_id, CartItem.product_id == product_id
        )
    )
    db.commit()
    return {"in_cart": False, **_summary(db, session_id)}


@router.post("/cart/remove/{product_id}")
def remove_from_cart_form(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Form-post twin of the DELETE endpoint — HTML forms cannot send DELETE."""
    remove_from_cart(product_id, request, db)
    return RedirectResponse("/cart", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/cart", response_class=HTMLResponse)
def cart_page(
    request: Request,
    error: str = "",
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
):
    from app.api.routes.pages import render, signal_context

    session_id = get_session_id(request)
    items = [i for i in cart_items(db, session_id) if i.product is not None]
    return render(
        request,
        "cart.html",
        user,
        {
            "items": items,
            "error": error,
            "total": round(sum(float(i.product.price or 0) for i in items), 2),
            "categories": [],
            **signal_context(db, user, session_id),
        },
        db=db,
    )


@router.post("/cart/checkout")
def checkout(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    full_name: str = Form(""),
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
):
    """Turn the cart into enrolments — no account required.

    A guest's enrolments are keyed by session, exactly like their behaviour and
    their recommendations. Creating an account later claims them
    (:func:`claim_guest_records`) rather than losing them.
    """
    from app.audience import Audience
    from app.api.routes.auth_routes import rotate_anon_session

    session_id = get_session_id(request)

    # Optional: make an account as part of checking out. Leaving the fields
    # blank checks out as a guest — the account is an offer, not a toll gate.
    created: User | None = None
    if user is None and email.strip() and password:
        created, error = _create_account(db, email, password, confirm_password, full_name)
        if error:
            return RedirectResponse(
                f"/cart?error={quote(error)}", status_code=status.HTTP_303_SEE_OTHER
            )
        user = created

    audience = Audience.of(user, session_id)
    if not audience.is_valid:
        return RedirectResponse("/cart", status_code=status.HTTP_303_SEE_OTHER)

    items = cart_items(db, session_id)
    if not items:
        return RedirectResponse("/cart", status_code=status.HTTP_303_SEE_OTHER)

    owned = set(db.scalars(select(Enrollment.product_id).where(owns_enrollment(audience))))
    added = 0
    for item in items:
        if item.product_id in owned or item.product is None:
            continue
        db.add(
            Enrollment(
                user_id=audience.user_id,
                session_id=None if audience.user_id else session_id,
                product_id=item.product_id,
            )
        )
        item.product.enrollments = (item.product.enrollments or 0) + 1
        added += 1

    db.execute(delete(CartItem).where(CartItem.session_id == session_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()

    logger.info("Checked out %d course(s) for %s", added, audience.key)
    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    if created is not None:
        # Everything they did as a guest — browsing, cart, these enrolments —
        # moves onto the new account before the session id is rotated away.
        claim_guest_records(db, created, session_id)
        _set_session(response, created)
        rotate_anon_session(response)
    return response


def _create_account(
    db: Session, email: str, password: str, confirm_password: str, full_name: str
) -> tuple[User | None, str]:
    """Create an account mid-checkout. Returns (user, error_message)."""
    from app.api.schemas import MIN_PASSWORD_LENGTH, RegisterIn
    from app.security import hash_password

    try:
        payload = RegisterIn(
            email=email,
            full_name=full_name,
            password=password,
            confirm_password=confirm_password,
        )
    except ValidationError as exc:
        if any("passwords do not match" in str(e.get("msg", "")) for e in exc.errors()):
            return None, "The two passwords do not match."
        return None, (
            f"Enter a valid email and a password of at least "
            f"{MIN_PASSWORD_LENGTH} characters."
        )

    normalized = payload.email.lower()
    if db.scalar(select(User).where(User.email == normalized)):
        return None, "That email is already registered — sign in and check out again."

    account = User(
        email=normalized,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role="user",
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account, ""


def claim_guest_records(db: Session, user: User, session_id: str) -> int:
    """Attach this session's guest enrolments and cart to the account.

    Called on sign-in and registration. Without it, everything someone did
    before making an account would silently belong to a session they can no
    longer be identified by.
    """
    if not session_id:
        return 0

    owned = set(db.scalars(select(Enrollment.product_id).where(Enrollment.user_id == user.id)))
    claimed = 0
    guest_rows = db.scalars(
        select(Enrollment).where(
            Enrollment.session_id == session_id, Enrollment.user_id.is_(None)
        )
    ).all()
    for row in guest_rows:
        if row.product_id in owned:
            db.delete(row)  # they already own it under the account
            continue
        row.user_id = user.id
        row.session_id = None
        claimed += 1

    db.execute(
        update(CartItem)
        .where(CartItem.session_id == session_id, CartItem.user_id.is_(None))
        .values(user_id=user.id)
    )
    # Their browsing so far is theirs: without this the agent would start from
    # nothing the moment they made an account.
    db.execute(
        update(Event)
        .where(Event.session_id == session_id, Event.user_id.is_(None))
        .values(user_id=user.id)
    )
    db.execute(
        update(Recommendation)
        .where(Recommendation.session_id == session_id, Recommendation.user_id.is_(None))
        .values(user_id=user.id, session_id=None)
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return 0
    if claimed:
        logger.info("Claimed %d guest enrolment(s) for user %s", claimed, user.id)
    return claimed
