"""Recommendation API — what the dashboard panel reads and refreshes from."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.api.schemas import RecommendationStatusOut
from app.database import get_db
from app.models import User
from app.service import generate_recommendation, recommendation_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.get("", response_model=RecommendationStatusOut)
def get_current(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> RecommendationStatusOut:
    """The user's current recommendation, or an honest reason there isn't one.

    Read-only and cheap — the dashboard polls this. Generation happens in the
    worker, never here.
    """
    return RecommendationStatusOut(**recommendation_status(db, user))


@router.post("/refresh", response_model=RecommendationStatusOut)
def refresh(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> RecommendationStatusOut:
    """Explicit user-initiated refresh.

    Queued to the worker so the request returns immediately. If the broker is
    down we fall back to generating inline rather than telling the user no.
    """
    try:
        from app.workers.tasks import generate_recommendation_task

        generate_recommendation_task.delay(user.id, "manual_refresh", True)
        payload = recommendation_status(db, user)
        payload["message"] = "Refreshing — the agent is re-reading your activity."
        payload["status"] = "pending" if payload["status"] != "ready" else "ready"
        return RecommendationStatusOut(**payload)
    except Exception:
        logger.warning("Broker unavailable; generating inline for user=%s", user.id)

    try:
        generate_recommendation(db, user.id, trigger="manual_refresh", force=True)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Inline generation failed for user=%s", user.id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Could not generate a recommendation right now: {type(exc).__name__}",
        ) from exc

    return RecommendationStatusOut(**recommendation_status(db, user))
