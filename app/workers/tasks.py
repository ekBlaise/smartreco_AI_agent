"""Celery tasks: event flush, recommendation generation, vector reconcile, digest."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from app.config import settings
from app.database import session_scope
from app.ingest import buffer, triggers
from app.service import (
    generate_recommendation,
    recommendation_payload,
    users_with_recent_activity,
)
from app.vector import sync
from app.workers.celery_app import celery_app
from app.workers.email import render_digest, send_digest

logger = logging.getLogger(__name__)


@celery_app.task(name="smartreco.flush_event_buffer")
def flush_event_buffer() -> dict[str, int]:
    """Drain the Redis event buffer into Postgres in one bulk insert."""
    result = buffer.flush_buffer()
    # A backlog means the 10s cadence isn't keeping up — drain harder rather
    # than letting the buffer grow unbounded.
    while result["remaining"] > settings.event_flush_batch_size:
        extra = buffer.flush_buffer()
        if not extra["flushed"]:
            break
        result["flushed"] += extra["flushed"]
        result["invalid"] += extra["invalid"]
        result["remaining"] = extra["remaining"]
    return result


@celery_app.task(
    name="smartreco.generate_recommendation",
    bind=True,
    max_retries=2,
    retry_backoff=15,
    retry_jitter=True,
    autoretry_for=(ConnectionError, TimeoutError),
)
def generate_recommendation_task(
    self, user_id: int, trigger: str = "behavior", force: bool = False
) -> dict[str, Any]:
    """Run the agent for one user. Queued by the tracking endpoint, never inline."""
    try:
        with session_scope() as session:
            recommendation, decision = generate_recommendation(
                session, user_id, trigger=trigger, force=force
            )
            if recommendation is None:
                return {"user_id": user_id, "generated": False, "reason": decision.reason}
            return {
                "user_id": user_id,
                "generated": True,
                "recommendation_id": recommendation.id,
                "version": recommendation.version,
                "items": len(recommendation.items),
            }
    except Exception:
        # Never leave a stale single-flight lock behind — it would block this
        # user's recommendations until the TTL expired.
        triggers.clear_lock(user_id)
        logger.exception("generate_recommendation failed for user=%s", user_id)
        raise


@celery_app.task(name="smartreco.reconcile_vector_store")
def reconcile_vector_store() -> dict[str, int]:
    """Re-push any product whose Qdrant copy is missing or older than the SQL row.

    This is what makes the dual-write durable: if Qdrant was down when an admin
    saved a product, the row is flagged and picked up here.
    """
    if not settings.mesh_configured:
        logger.warning("Skipping vector reconcile — MESH_API_KEY is not set")
        return {"embedded": 0, "skipped": 0, "failed": 0, "pending": 0}

    with session_scope() as session:
        stale = sync.find_out_of_sync(session)
        if not stale:
            return {"embedded": 0, "skipped": 0, "failed": 0, "pending": 0}
        logger.info("Reconciling %d out-of-sync products", len(stale))
        result = sync.sync_products(session, stale, force=True)
        result["pending"] = len(sync.find_out_of_sync(session))
        return result


@celery_app.task(name="smartreco.send_daily_digest")
def send_daily_digest() -> dict[str, Any]:
    """Proactive delivery: a personalised recap of the day's interests.

    Runs the same agent as the on-site recommendation, so the email is grounded
    in the real catalog and reflects today's behaviour rather than a stale list.
    """
    if not settings.digest_enabled:
        return {"enabled": False, "sent": 0}

    with session_scope() as session:
        user_ids = users_with_recent_activity(session, hours=24)

    logger.info("Daily digest: %d users with enough activity", len(user_ids))
    sent = dry_run = skipped = failed = 0

    for user_id in user_ids:
        try:
            with session_scope() as session:
                from app.models import User

                user = session.get(User, user_id)
                if user is None or not user.is_active:
                    skipped += 1
                    continue

                recommendation, decision = generate_recommendation(
                    session, user_id, trigger="daily_digest", force=True
                )
                if recommendation is None:
                    # Fall back to the current stored recommendation so an
                    # engaged user still gets their digest.
                    recommendation = triggers.current_recommendation(session, user_id)
                if recommendation is None:
                    skipped += 1
                    continue

                subject, html = render_digest(user, recommendation_payload(recommendation))
                email = user.email
            result = send_digest(email, subject, html)
            if result.get("dry_run"):
                dry_run += 1
            elif result.get("sent"):
                sent += 1
            else:
                failed += 1
        except Exception:
            failed += 1
            logger.exception("Digest failed for user=%s", user_id)

    summary = {
        "enabled": True,
        "candidates": len(user_ids),
        "sent": sent,
        "dry_run": dry_run,
        "skipped": skipped,
        "failed": failed,
    }
    logger.info("Daily digest complete: %s", summary)
    return summary


@celery_app.task(name="smartreco.sync_product")
def sync_product_task(product_id: int) -> dict[str, Any]:
    """Retry a single product's vector write out of band (used by admin CRUD)."""
    from app.models import Product

    with session_scope() as session:
        product = session.get(Product, product_id)
        if product is None:
            return {"product_id": product_id, "found": False}
        result = sync.sync_product(session, product, force=True)
        return {"product_id": product_id, "found": True, **result}
