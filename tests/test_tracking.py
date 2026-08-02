"""Event ingest: fast, batched, non-blocking, and impossible to break."""

from __future__ import annotations

from sqlalchemy import func, select

from app.config import settings
from app.ingest import buffer
from app.models import Event


def _batch(*events):
    return {"events": list(events)}


def test_ingest_buffers_instead_of_writing_inline(client, catalog, db, fake_redis):
    """The request path must not touch Postgres."""
    response = client.post(
        "/api/events/batch",
        json=_batch(
            {"type": "product_view", "product_id": catalog[0].id},
            {"type": "search", "query": "langgraph agents"},
        ),
    )

    assert response.status_code == 202
    body = response.json()
    assert body == {"accepted": 2, "dropped": 0, "buffered": True}

    # Buffered in Redis, not yet in the database.
    assert fake_redis.llen(settings.event_buffer_key) == 2
    assert db.scalar(select(func.count(Event.id))) == 0


def test_flush_writes_the_whole_batch_at_once(client, catalog, db):
    client.post(
        "/api/events/batch",
        json=_batch(
            {"type": "product_view", "product_id": catalog[0].id},
            {"type": "product_click", "product_id": catalog[1].id},
            {"type": "search", "query": "multi agent orchestration"},
            {"type": "dwell", "product_id": catalog[0].id, "dwell_ms": 45000},
        ),
    )

    result = buffer.flush_buffer()

    assert result["flushed"] == 4
    assert result["remaining"] == 0
    assert db.scalar(select(func.count(Event.id))) == 4


def test_weights_are_assigned_on_ingest(client, catalog, db):
    client.post(
        "/api/events/batch",
        json=_batch(
            {"type": "search", "query": "langgraph"},
            {"type": "dwell", "product_id": catalog[0].id, "dwell_ms": 45000},
            {"type": "page_view"},
        ),
    )
    buffer.flush_buffer()

    weights = {e.type: e.weight for e in db.scalars(select(Event))}
    assert weights["search"] == 3
    assert weights["dwell"] == 3, "long dwell gets the attention bonus"
    assert weights["page_view"] == 0, "an incidental page view is not a signal"


def test_malformed_events_are_dropped_not_fatal(client, catalog, db):
    """One bad event must not cost the good ones, or surface as an error."""
    response = client.post(
        "/api/events/batch",
        json=_batch(
            {"type": "product_view", "product_id": catalog[0].id},
            {"type": "not_a_real_event_type"},
            {"type": "search", "dwell_ms": -50},
            {"nonsense": True},
            {"type": "search", "query": "kafka streaming"},
        ),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 2
    assert body["dropped"] == 3

    buffer.flush_buffer()
    assert db.scalar(select(func.count(Event.id))) == 2


def test_garbage_body_does_not_500(client):
    assert client.post("/api/events/batch", content=b"not json").status_code == 202
    assert client.post("/api/events/batch", json={"events": "nope"}).status_code == 202
    assert client.post("/api/events/batch", json=[]).status_code == 202


def test_oversized_batch_is_capped(client, catalog):
    huge = [{"type": "page_view"} for _ in range(200)]
    response = client.post("/api/events/batch", json={"events": huge})

    assert response.status_code == 202
    assert response.json()["accepted"] == settings.event_max_batch_payload


def test_tracking_survives_redis_being_down(client, catalog, db, monkeypatch):
    """Falls back to a direct write rather than losing the events.

    ``get_redis`` is patched rather than just clearing the cached client, so the
    unavailable path is exercised even on a machine where Redis is genuinely
    running.
    """
    monkeypatch.setattr("app.ingest.buffer.get_redis", lambda: None)

    response = client.post(
        "/api/events/batch",
        json=_batch({"type": "product_view", "product_id": catalog[0].id}),
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": 1, "dropped": 0, "buffered": False}
    assert db.scalar(select(func.count(Event.id))) == 1


def test_events_from_a_signed_in_user_are_attributed(logged_in, catalog, db, learner):
    logged_in.post(
        "/api/events/batch",
        json=_batch({"type": "product_view", "product_id": catalog[0].id}),
    )
    buffer.flush_buffer()

    event = db.scalar(select(Event))
    assert event.user_id == learner.id
    assert event.session_id, "anonymous session id is still recorded"


def test_anonymous_events_are_kept_against_a_session(client, catalog, db):
    client.post("/api/events/batch", json=_batch({"type": "product_view", "product_id": catalog[0].id}))
    buffer.flush_buffer()

    event = db.scalar(select(Event))
    assert event.user_id is None
    assert len(event.session_id) == 32


def test_event_for_a_deleted_product_is_kept_without_the_fk(client, catalog, db):
    """A stale product_id must not fail the whole bulk insert."""
    client.post("/api/events/batch", json=_batch({"type": "product_view", "product_id": 999999}))
    result = buffer.flush_buffer()

    assert result["flushed"] == 1
    assert db.scalar(select(Event)).product_id is None
