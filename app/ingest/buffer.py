"""Event buffering: accept fast, persist in bulk.

The tracking endpoint must never make the site feel slow, so it does the minimum
possible work: validate, then ``RPUSH`` the batch onto a Redis list and return
202. A Celery Beat task drains the list every few seconds and writes the rows to
Postgres with a single bulk insert.

If Redis is unreachable the endpoint falls back to writing directly to Postgres.
That is slower, but tracking silently breaking is the worse failure.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache import get_redis
from app.config import settings
from app.database import session_scope
from app.models import EVENT_WEIGHTS, Event, Product

logger = logging.getLogger(__name__)


def _serialize(record: dict[str, Any]) -> str:
    return json.dumps(record, default=str)


def _normalize(record: dict[str, Any]) -> dict[str, Any] | None:
    """Coerce a buffered payload into an ``events`` row. Returns None if unusable."""
    event_type = record.get("type")
    if not event_type or event_type not in EVENT_WEIGHTS:
        return None

    occurred_at = record.get("occurred_at")
    if isinstance(occurred_at, str):
        try:
            occurred_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            occurred_at = None
    if not isinstance(occurred_at, datetime):
        occurred_at = datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    dwell_ms = record.get("dwell_ms") or 0
    try:
        dwell_ms = max(0, int(dwell_ms))
    except (TypeError, ValueError):
        dwell_ms = 0

    weight = EVENT_WEIGHTS.get(event_type, 0)
    # Sustained attention is a stronger signal than a bounce.
    if event_type == "dwell" and dwell_ms >= 30_000:
        weight += 2

    product_id = record.get("product_id")
    try:
        product_id = int(product_id) if product_id is not None else None
    except (TypeError, ValueError):
        product_id = None

    return {
        "user_id": record.get("user_id"),
        "session_id": (record.get("session_id") or "")[:64],
        "type": event_type,
        "product_id": product_id,
        "query": (record.get("query") or None),
        "path": (record.get("path") or None),
        "dwell_ms": dwell_ms,
        "weight": weight,
        "meta": record.get("meta") or {},
        "occurred_at": occurred_at,
        "ingested_at": datetime.now(timezone.utc),
    }


def push_events(records: list[dict[str, Any]]) -> tuple[int, bool]:
    """Buffer a batch. Returns (accepted, buffered_in_redis)."""
    if not records:
        return 0, False

    client = get_redis()
    if client is not None:
        try:
            client.rpush(settings.event_buffer_key, *[_serialize(r) for r in records])
            return len(records), True
        except Exception:
            logger.warning("Redis buffer push failed; falling back to direct insert")

    with session_scope() as session:
        written = _bulk_insert(session, [r for r in map(_normalize, records) if r])
    return written, False


def _valid_product_ids(session: Session, rows: list[dict[str, Any]]) -> set[int]:
    ids = {r["product_id"] for r in rows if r.get("product_id")}
    if not ids:
        return set()
    return set(session.scalars(select(Product.id).where(Product.id.in_(ids))))


def _bulk_insert(session: Session, rows: list[dict[str, Any]]) -> int:
    """One INSERT for the whole batch."""
    if not rows:
        return 0
    # Drop FKs pointing at products that no longer exist rather than failing the batch.
    known = _valid_product_ids(session, rows)
    for row in rows:
        if row.get("product_id") and row["product_id"] not in known:
            row["product_id"] = None
    session.bulk_insert_mappings(Event, rows)
    return len(rows)


def flush_buffer(max_events: int | None = None) -> dict[str, int]:
    """Drain the Redis buffer into Postgres. Called by Celery Beat."""
    client = get_redis()
    if client is None:
        return {"flushed": 0, "invalid": 0, "remaining": 0}

    limit = max_events or settings.event_flush_batch_size
    try:
        raw = client.lpop(settings.event_buffer_key, limit)
    except Exception:
        logger.exception("Failed to pop from event buffer")
        return {"flushed": 0, "invalid": 0, "remaining": 0}

    if not raw:
        return {"flushed": 0, "invalid": 0, "remaining": 0}
    if isinstance(raw, str):
        raw = [raw]

    rows: list[dict[str, Any]] = []
    invalid = 0
    for item in raw:
        try:
            record = json.loads(item)
        except (TypeError, ValueError):
            invalid += 1
            continue
        normalized = _normalize(record)
        if normalized is None:
            invalid += 1
            continue
        rows.append(normalized)

    with session_scope() as session:
        flushed = _bulk_insert(session, rows)

    try:
        remaining = int(client.llen(settings.event_buffer_key))
    except Exception:
        remaining = 0

    if flushed:
        logger.info("Flushed %d events (%d invalid, %d queued)", flushed, invalid, remaining)
    return {"flushed": flushed, "invalid": invalid, "remaining": remaining}


def buffer_depth() -> int:
    client = get_redis()
    if client is None:
        return 0
    try:
        return int(client.llen(settings.event_buffer_key))
    except Exception:
        return 0
