"""The app has to work when only `uvicorn` is running.

Every screen reads Postgres, but tracking writes to a Redis buffer. Without
something draining that buffer the whole product looks broken while erroring
nowhere: "0 actions tracked", "0 versions generated", and an agent that never
runs. These cover the two fallbacks that close that gap, and the backoff that
stops a dead Mesh account from retrying forever.
"""

from __future__ import annotations

from app.audience import Audience

import asyncio

import pytest
from sqlalchemy import func, select

from app.api.routes import events as events_route
from app.ingest import drain
from app.llm import mesh
from app.models import AgentRun, Event
from app.service import generate_recommendation, recommendation_status
from tests.test_triggers import AGENTIC_SESSION, add_events


# --- draining the buffer without Celery --------------------------------------

def test_the_api_drains_the_buffer_itself(logged_in, catalog, db, fake_redis):
    """Buffered events must reach Postgres with no worker running."""
    logged_in.post(
        "/api/events/batch",
        json={"events": [
            {"type": "product_view", "product_id": catalog[0].id},
            {"type": "search", "query": "langgraph agents"},
        ]},
    )
    assert db.scalar(select(func.count(Event.id))) == 0, "buffered, not yet stored"

    result = asyncio.run(drain.drain_once())

    assert result["flushed"] == 2
    assert db.scalar(select(func.count(Event.id))) == 2


def test_draining_is_safe_when_there_is_nothing_to_drain(db, fake_redis):
    assert asyncio.run(drain.drain_once())["flushed"] == 0


def test_draining_does_nothing_without_redis(db, monkeypatch):
    """No Redis means push_events already wrote straight through."""
    monkeypatch.setattr(drain, "get_redis", lambda: None)

    assert asyncio.run(drain.drain_once())["flushed"] == 0


def test_only_one_process_drains_at_a_time(logged_in, catalog, db, fake_redis, monkeypatch):
    """The lock stops several API workers double-flushing the same batch."""
    logged_in.post(
        "/api/events/batch",
        json={"events": [{"type": "product_view", "product_id": catalog[0].id}]},
    )
    monkeypatch.setattr(drain, "acquire_lock", lambda *a, **k: False)

    assert asyncio.run(drain.drain_once())["flushed"] == 0
    assert db.scalar(select(func.count(Event.id))) == 0, "left for the lock holder"


# --- Mesh backoff -------------------------------------------------------------

def test_a_billing_refusal_is_recognised():
    err = Exception(
        "Error code: 402 - {'error': {'code': 'spend_limit_exceeded', "
        "'message': 'Insufficient balance.'}}"
    )
    assert mesh.is_billing_error(err)
    assert not mesh.is_billing_error(Exception("Connection reset by peer"))


def test_a_billing_refusal_pauses_further_calls(db, learner, catalog, fake_mesh, fake_redis):
    """Retrying an empty account fails identically every minute and buries real
    errors in stack traces, so it must stop trying."""
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()
    fake_mesh.fail_with(
        Exception("Error code: 402 - {'error': {'code': 'spend_limit_exceeded'}}")
    )

    recommendation, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    assert recommendation is None
    assert mesh.unavailable_reason(), "Mesh should be marked unavailable"

    run = db.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
    assert run.status == "billing"


def test_the_dashboard_explains_a_paused_agent(db, learner, catalog, fake_redis):
    """Better than spinning "the agent is reading your activity" forever."""
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()
    mesh.mark_unavailable("Mesh has no balance — add credit.", 600)

    status = recommendation_status(db, Audience(user_id=learner.id))

    assert status["status"] == "unavailable"
    assert "balance" in status["message"]


def test_a_paused_mesh_stops_the_agent_being_queued(logged_in, catalog, db, fake_redis, monkeypatch):
    monkeypatch.setattr(events_route, "_workers_online", lambda: False)
    mesh.mark_unavailable("no balance", 600)

    logged_in.post(
        "/api/events/batch",
        json={"events": [{"type": "product_view", "product_id": catalog[0].id}]},
    )

    assert db.scalars(select(AgentRun)).all() == []


@pytest.fixture(autouse=True)
def _clear_mesh_block():
    mesh.clear_unavailable()
    yield
    mesh.clear_unavailable()
