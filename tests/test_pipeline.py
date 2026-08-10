"""End-to-end: browsing a site produces a recommendation.

The unit tests cover each stage in isolation; this covers the seam between them,
which is where the interesting bugs live — the tracking endpoint decides to wake
the agent, and the Celery task must actually be able to run when it does.
"""

from __future__ import annotations

from sqlalchemy import select

from app.api.routes import events as events_route
from app.models import AgentRun, Recommendation
from app.workers.tasks import flush_event_buffer, generate_recommendation_task


def browse_like_an_agentic_ai_learner(client, catalog) -> None:
    """A realistic session: views, two searches, a click, an enrol intent."""
    client.post(
        "/api/events/batch",
        json={"events": [
            {"type": "page_view", "path": "/catalog"},
            {"type": "product_view", "product_id": catalog[0].id},
            {"type": "search", "query": "langgraph agents"},
            {"type": "product_view", "product_id": catalog[1].id},
            {"type": "search", "query": "multi agent orchestration"},
            {"type": "dwell", "product_id": catalog[1].id, "dwell_ms": 48000},
            {"type": "product_click", "product_id": catalog[2].id},
            {"type": "enroll_intent", "product_id": catalog[1].id},
        ]},
    )


def test_browsing_produces_a_recommendation_with_no_worker_running(
    logged_in, catalog, db, learner, monkeypatch, fake_mesh
):
    """Track → flush → trigger → dispatch → stored recommendation, on `uvicorn` alone.

    This is how anyone first runs the project. With no Celery worker consuming,
    the run has to happen in-process or the agent silently never fires — which
    is exactly the bug this covers.
    """
    monkeypatch.setattr(events_route, "_workers_online", lambda: False)

    browse_like_an_agentic_ai_learner(logged_in, catalog)
    flush_event_buffer()

    # The first batch was buffered, so the trigger had nothing to score yet.
    # A second batch after the flush is what a real session looks like.
    logged_in.post(
        "/api/events/batch",
        json={"events": [{"type": "product_view", "product_id": catalog[1].id}]},
    )

    db.expire_all()
    recommendation = db.scalar(
        select(Recommendation).where(Recommendation.user_id == learner.id)
    )
    assert recommendation is not None, "no recommendation was produced without a worker"
    assert recommendation.items
    assert recommendation.is_current is True


def test_a_run_is_handed_to_celery_when_a_worker_is_consuming(
    logged_in, catalog, db, learner, monkeypatch, fake_mesh
):
    """With a worker online the run must go to the queue, not run in the web
    process — otherwise the fallback would quietly become the only path."""
    monkeypatch.setattr(events_route, "_workers_online", lambda: True)
    sent: list = []
    monkeypatch.setattr(
        generate_recommendation_task, "apply_async",
        lambda args=None, **kw: sent.append(args),
    )

    browse_like_an_agentic_ai_learner(logged_in, catalog)
    flush_event_buffer()
    logged_in.post(
        "/api/events/batch",
        json={"events": [{"type": "product_view", "product_id": catalog[1].id}]},
    )

    assert sent, "a worker was available but nothing was queued"
    assert sent[0][0] == learner.id
    db.expire_all()
    assert db.scalars(select(Recommendation)).all() == [], "should not have run inline"


def test_a_burst_of_batches_queues_one_run_not_many(
    logged_in, catalog, db, learner, monkeypatch, fake_mesh
):
    """Ten rapid batches must not become ten agent runs."""
    monkeypatch.setattr(events_route, "_workers_online", lambda: False)

    browse_like_an_agentic_ai_learner(logged_in, catalog)
    flush_event_buffer()

    for _ in range(10):
        logged_in.post(
            "/api/events/batch",
            json={"events": [{"type": "product_view", "product_id": catalog[1].id}]},
        )

    db.expire_all()
    stored = db.scalars(
        select(Recommendation).where(Recommendation.user_id == learner.id)
    ).all()

    assert len(stored) == 1, f"expected exactly one recommendation, got {len(stored)}"
    # Count real runs, not a transport call: this must stay true however the
    # run is dispatched.
    runs = db.scalars(select(AgentRun).where(AgentRun.status == "ok")).all()
    assert len(runs) <= 2, f"ran the agent {len(runs)} times for one burst"


def test_idle_browsing_never_queues_a_run(logged_in, catalog, db, monkeypatch, fake_mesh):
    monkeypatch.setattr(events_route, "_workers_online", lambda: False)

    for _ in range(6):
        logged_in.post(
            "/api/events/batch",
            json={"events": [
                {"type": "page_view", "path": "/catalog"},
                {"type": "scroll_depth", "meta": {"depth": 50}},
            ]},
        )
        flush_event_buffer()

    assert db.scalars(select(AgentRun)).all() == [], "scrolling must not wake the agent"
    assert fake_mesh.call_count == 0
