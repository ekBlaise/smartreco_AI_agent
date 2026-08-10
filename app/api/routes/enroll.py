"""Enrolment.

Deliberately no payment. This is a demo marketplace, so "Enrol" records that a
learner committed to a course and nothing more — it never asks for card details.
The commitment is still real state: it persists, it shows on the course page and
the dashboard, and the agent stops recommending courses the learner already has.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_session_id, require_user
from app.database import get_db
from app.ingest import buffer
from app.models import Enrollment, Product, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/enroll", tags=["enrollment"])


@router.post("/{product_id}")
def enroll(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    product = db.get(Product, product_id)
    if product is None or not product.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found")

    existing = db.scalar(
        select(Enrollment).where(
            Enrollment.user_id == user.id, Enrollment.product_id == product_id
        )
    )
    if existing is not None:
        # Pressing Enrol twice is not an error — report the state, not a 409.
        return _state(product, enrolled=True, created=False)

    db.add(Enrollment(user_id=user.id, product_id=product_id))
    product.enrollments = (product.enrollments or 0) + 1
    try:
        db.commit()
    except IntegrityError:
        # Two tabs, one learner. The unique constraint is the referee.
        db.rollback()
        return _state(product, enrolled=True, created=False)

    # The strongest behavioural signal there is, recorded through the same
    # buffered path as every other event so it reaches the agent's profile.
    buffer.push_events(
        [
            {
                "user_id": user.id,
                "session_id": get_session_id(request),
                "type": "enroll_intent",
                "product_id": product_id,
                "query": None,
                "path": f"/course/{product.slug}",
                "dwell_ms": 0,
                "meta": {"category": product.category, "surface": "enroll_button"},
                "occurred_at": None,
            }
        ]
    )
    logger.info("User %s enrolled in product %s", user.id, product_id)
    return _state(product, enrolled=True, created=True)


def _state(product: Product, *, enrolled: bool, created: bool) -> dict:
    return {
        "enrolled": enrolled,
        "created": created,
        "product_id": product.id,
        "title": product.title,
        "message": (
            f"You're enrolled in {product.title}."
            if created
            else f"You're already enrolled in {product.title}."
        ),
    }
