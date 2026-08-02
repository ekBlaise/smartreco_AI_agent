"""End-to-end: browsing a site produces a recommendation.

The unit tests cover each stage in isolation; this covers the seam between them,
which is where the interesting bugs live — the tracking endpoint decides to wake
the agent, and the Celery task must actually be able to run when it does.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import Recommendation
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


def test_browsing_produces_a_recommendation_through_the_real_path(
    logged_in, catalog, db, learner, monkeypatch
):
    """Track → flush → trigger → queue → task → stored recommendation.

    Celery runs eagerly here so the queued task executes inline, exercising the
    same code the worker would.
    """
    queued: list[tuple] = []

    def capture(user_id, trigger="behavior", force=False):
        queued.append((user_id, trigger, force))
        return generate_recommendation_task.run(user_id, trigger, force)

    monkeypatch.setattr(generate_recommendation_task, "delay", capture)

    browse_like_an_agentic_ai_learner(logged_in, catalog)
    flush_event_buffer()

    # The first batch was buffered, so the trigger had nothing to score yet.
    # A second batch after the flush is what a real session looks like.
    logged_in.post(
        "/api/events/batch",
        json={"events": [{"type": "product_view", "product_id": catalog[1].id}]},
    )

    assert queued, "the tracking endpoint never queued an agent run"

    db.expire_all()
    recommendation = db.scalar(
        select(Recommendation).where(Recommendation.user_id == learner.id)
    )
    assert recommendation is not None, "the queued task did not produce a recommendation"
    assert recommendation.items
    assert recommendation.is_current is True


def test_a_burst_of_batches_queues_one_run_not_many(
    logged_in, catalog, db, learner, monkeypatch, fake_mesh
):
    """Ten rapid batches must not become ten agent runs."""
    queued: list[tuple] = []

    def capture(user_id, trigger="behavior", force=False):
        queued.append((user_id, trigger, force))
        return generate_recommendation_task.run(user_id, trigger, force)

    monkeypatch.setattr(generate_recommendation_task, "delay", capture)

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
    assert len(queued) <= 2, f"queued {len(queued)} agent runs for one burst"


def test_idle_browsing_never_queues_a_run(logged_in, catalog, db, monkeypatch, fake_mesh):
    queued: list = []
    monkeypatch.setattr(
        generate_recommendation_task, "delay", lambda *a, **k: queued.append(a)
    )

    for _ in range(6):
        logged_in.post(
            "/api/events/batch",
            json={"events": [
                {"type": "page_view", "path": "/catalog"},
                {"type": "scroll_depth", "meta": {"depth": 50}},
            ]},
        )
        flush_event_buffer()

    assert queued == [], "scrolling around must not wake the agent"
    assert fake_mesh.call_count == 0
