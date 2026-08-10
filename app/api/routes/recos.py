"""Recommendation API — what the dashboard panel reads and refreshes from."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import current_user_optional, get_session_id
from app.api.schemas import RecommendationStatusOut
from app.database import get_db
from app.audience import Audience
from app.models import User
from app.service import generate_recommendation, recommendation_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.get("", response_model=RecommendationStatusOut)
def get_current(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
) -> RecommendationStatusOut:
    """The user's current recommendation, or an honest reason there isn't one.

    Read-only and cheap — the dashboard polls this. Generation happens in the
    worker, never here.
    """
    return RecommendationStatusOut(
        **recommendation_status(db, Audience.of(user, get_session_id(request)))
    )


@router.post("/refresh", response_model=RecommendationStatusOut)
def refresh(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
) -> RecommendationStatusOut:
    """Explicit user-initiated refresh.

    Queued to the worker so the request returns immediately. If the broker is
    down we fall back to generating inline rather than telling the user no.
    """
    audience = Audience.of(user, get_session_id(request))
    try:
        from app.workers.tasks import generate_recommendation_task

        generate_recommendation_task.delay(
            audience.user_id, "manual_refresh", True, audience.session_id
        )
        payload = recommendation_status(db, audience)
        payload["message"] = "Refreshing — the agent is re-reading your activity."
        payload["status"] = "pending" if payload["status"] != "ready" else "ready"
        return RecommendationStatusOut(**payload)
    except Exception:
        logger.warning("Broker unavailable; generating inline for %s", audience.key)

    try:
        generate_recommendation(db, audience, trigger="manual_refresh", force=True)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Inline generation failed for %s", audience.key)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Could not generate a recommendation right now: {type(exc).__name__}",
        ) from exc

    return RecommendationStatusOut(**recommendation_status(db, audience))
