"""Behavioural event ingest.

Design goal: this endpoint must be boring and fast. It validates, buffers, and
returns 202. It never embeds, never calls an LLM, and never blocks on Postgres
when Redis is available. The decision about whether the agent should run is made
here too — but only as a cheap scoring check, with the actual generation handed
to Celery.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy.orm import Session

from app import realtime
from app.audience import Audience
from app.api.deps import current_user_optional, get_session_id
from app.api.schemas import EventBatchIn, EventBatchOut
from app.cache import (
    acquire_lock,
    cache_get_json,
    cache_set_json,
    reco_queued_key,
    release_lock,
)
from app.config import settings
from app.database import get_db
from app.ingest import buffer, triggers
from app.llm import mesh
from app.models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/events", tags=["events"])

#: How long one queued agent run suppresses further enqueueing for a user.
QUEUE_DEDUPE_SECONDS = 60

#: Cached answer to "is a Celery worker consuming?", so the ping is rare.
WORKERS_ONLINE_KEY = "smartreco:workers:online"
WORKERS_ONLINE_TTL = 30


@router.post(
    "/batch",
    response_model=EventBatchOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_batch(
    request: Request,
    background: BackgroundTasks,
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
    # Runs for signed-out visitors too: their behaviour is tracked the same way,
    # so it deserves the same reasoning.
    audience = Audience.of(user, session_id)
    if accepted and audience.is_valid:
        _maybe_queue_generation(db, audience, background)

    return EventBatchOut(accepted=accepted, dropped=dropped, buffered=buffered)


def _maybe_queue_generation(
    db: Session, audience: Audience, background: BackgroundTasks | None = None
) -> None:
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
        decision = triggers.evaluate(db, audience, acquire=False)
    except Exception:
        logger.exception("Trigger evaluation failed for %s", audience.key)
        return

    if not decision.should_generate:
        return

    blocked = mesh.unavailable_reason()
    if blocked:
        logger.debug("Skipping generation for %s: %s", audience.key, blocked)
        return

    if not acquire_lock(reco_queued_key(audience.key), QUEUE_DEDUPE_SECONDS):
        logger.debug("Agent run already queued for %s", audience.key)
        return

    # Let any open tab show "agent thinking" straight away, rather than the
    # panel sitting silent for however long the run takes.
    realtime.publish_agent_state(audience.key, "thinking", decision.reason)

    if background is None:  # pragma: no cover - background is always injected
        _dispatch_generation(audience, decision.reason)
        return

    # Deciding *where* to run costs a worker ping, so it happens after the
    # response is sent. The endpoint stays as fast as it was.
    background.add_task(_dispatch_generation, audience, decision.reason)


def _dispatch_generation(audience: Audience, trigger: str) -> None:
    """Hand the run to Celery if a worker is really consuming; else run it here.

    Testing the *broker* is not enough. Redis is the broker, so when Redis is up
    but no worker is running, ``apply_async`` succeeds into a queue nobody
    reads — and the recommendation silently never happens. That is precisely the
    state anyone gets by starting only `uvicorn`, which is how the project is
    first tried.
    """
    if settings.inline_generation_fallback and not _workers_online():
        logger.info("No Celery worker; generating inline for %s (%s)", audience.key, trigger)
        _generate_inline(audience, trigger)
        return

    try:
        from app.workers.tasks import generate_recommendation_task

        # retry=False so an unreachable broker fails fast rather than working
        # through Celery's connection retry policy.
        generate_recommendation_task.apply_async(
            args=[audience.user_id, trigger, False, audience.session_id], retry=False
        )
        logger.info("Queued recommendation for %s (%s)", audience.key, trigger)
    except Exception:
        logger.warning("Could not queue for %s; running inline", audience.key)
        if settings.inline_generation_fallback:
            _generate_inline(audience, trigger)
        else:
            release_lock(reco_queued_key(audience.key))


def _workers_online() -> bool:
    """Cached worker liveness. The ping is a round-trip; do not pay it often."""
    cached = cache_get_json(WORKERS_ONLINE_KEY)
    if cached is not None:
        return bool(cached)

    from app import health

    online = bool(health.ping_workers(timeout=0.5))
    cache_set_json(WORKERS_ONLINE_KEY, online, WORKERS_ONLINE_TTL)
    return online


def _generate_inline(audience: Audience, trigger: str) -> None:
    """Fallback generation, on its own session and its own thread.

    FastAPI runs a sync background task in a threadpool, so the minute or so
    this can take does not block the event loop.
    """
    from app.database import session_scope
    from app.service import generate_recommendation

    try:
        with session_scope() as session:
            generate_recommendation(session, audience, trigger=trigger)
    except Exception:
        logger.exception("Inline generation failed for %s", audience.key)
    finally:
        release_lock(reco_queued_key(audience.key))
