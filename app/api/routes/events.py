"""Behavioural event ingest.

Design goal: this endpoint must be boring and fast. It validates, buffers, and
returns 202. It never embeds, never calls an LLM, and never blocks on Postgres
when Redis is available. The decision about whether the agent should run is made
here too — but only as a cheap scoring check, with the actual generation handed
to Celery.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import current_user_optional, get_session_id
from app.api.schemas import EventBatchIn, EventBatchOut
from app.cache import acquire_lock, reco_queued_key, release_lock
from app.config import settings
from app.database import get_db
from app.ingest import buffer, triggers
from app.models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/events", tags=["events"])

#: How long one queued agent run suppresses further enqueueing for a user.
QUEUE_DEDUPE_SECONDS = 60


@router.post(
    "/batch",
    response_model=EventBatchOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_batch(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
) -> EventBatchOut:
    """Accept a batch of tracked events.

    Parsed leniently on purpose: one malformed event in a batch of twenty must
    not cost the other nineteen, and must never surface as an error in the
    user's browser.
    """
    session_id = get_session_id(request)

    try:
        raw = await request.json()
    except Exception:
        return EventBatchOut(accepted=0, dropped=0, buffered=False)

    if isinstance(raw, list):
        raw = {"events": raw}
    if not isinstance(raw, dict):
        return EventBatchOut(accepted=0, dropped=0, buffered=False)

    incoming = raw.get("events") or []
    if not isinstance(incoming, list):
        return EventBatchOut(accepted=0, dropped=0, buffered=False)
    incoming = incoming[: settings.event_max_batch_payload]

    records = []
    dropped = 0
    for item in incoming:
        try:
            event = EventBatchIn(events=[item]).events[0]
        except Exception:
            dropped += 1
            continue
        records.append(
            {
                "user_id": user.id if user else None,
                "session_id": session_id,
                "type": event.type,
                "product_id": event.product_id,
                "query": event.query,
                "path": event.path,
                "dwell_ms": event.dwell_ms,
                "meta": event.meta,
                "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            }
        )

    accepted, buffered = buffer.push_events(records)

    # Cheap, LLM-free check on whether this activity is worth waking the agent.
    if user is not None and accepted:
        _maybe_queue_generation(db, user.id)

    return EventBatchOut(accepted=accepted, dropped=dropped, buffered=buffered)


def _maybe_queue_generation(db: Session, user_id: int) -> None:
    """Queue an agent run only if the trigger gates look likely to pass.

    This is a *pre-filter*, not the decision: a couple of indexed queries plus
    arithmetic, no model call, cheap enough to run on every batch. It runs with
    ``acquire=False`` — taking the single-flight lock here would mean the task
    we are about to queue gets turned away by our own lock. The worker
    re-evaluates authoritatively and owns the lock while it runs.

    Enqueueing is deduped separately: a burst of batches sets one short-lived
    "queued" marker between them, so ten rapid flushes become one task.
    """
    try:
        decision = triggers.evaluate(db, user_id, acquire=False)
    except Exception:
        logger.exception("Trigger evaluation failed for user=%s", user_id)
        return

    if not decision.should_generate:
        return

    if not acquire_lock(reco_queued_key(user_id), QUEUE_DEDUPE_SECONDS):
        logger.debug("Agent run already queued for user=%s", user_id)
        return

    try:
        from app.workers.tasks import generate_recommendation_task

        generate_recommendation_task.delay(user_id, decision.reason)
        logger.info("Queued recommendation for user=%s (%s)", user_id, decision.reason)
    except Exception:
        # Broker unavailable — drop the marker so the next batch can retry
        # rather than waiting out its TTL for a task that was never queued.
        release_lock(reco_queued_key(user_id))
        logger.warning("Could not queue recommendation task for user=%s", user_id)
