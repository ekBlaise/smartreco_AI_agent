"""The LangGraph agent: grounded retrieval, self-correction, and persistence."""

from __future__ import annotations

from app.audience import Audience

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.agent.graph import route_after_grading
from app.agent.state import RecommendationDraft, RecommendedItem
from app.models import AgentRun, Recommendation
from app.service import generate_recommendation
from tests.test_triggers import AGENTIC_SESSION, add_events


def test_agent_walks_the_full_graph_and_stores_a_recommendation(db, learner, catalog, fake_mesh):
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    recommendation, decision = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    assert decision.should_generate is True
    assert recommendation is not None
    assert recommendation.version == 1
    assert recommendation.is_current is True
    assert recommendation.headline
    assert recommendation.narrative
    assert recommendation.items, "the agent must recommend actual products"

    run = db.scalar(select(AgentRun))
    assert run.status == "ok"
    assert run.node_path == [
        "profile_behavior", "plan_queries", "retrieve",
        "grade_candidates", "generate", "finalize",
    ]
    assert run.llm_calls == 3, "profile + grade + generate — no wasted calls"
    assert run.candidates > 0


def test_recommended_products_come_from_the_real_catalog(db, learner, catalog):
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    recommendation, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    catalog_ids = {p.id for p in catalog}
    for item in recommendation.items:
        assert item.product_id in catalog_ids
        assert item.product is not None
        assert item.why_this, "each product needs its own persuasion"


def test_retrieval_is_behaviour_driven_not_generic(db, learner, catalog):
    """Someone deep in data engineering must not be sold the agentic track."""
    add_events(
        db, learner, catalog,
        ("search", None, "kafka streaming partitions consumers"),
        ("search", None, "dbt sql warehouse modeling"),
        ("product_view", 4, None),
        ("product_view", 5, None),
        ("product_click", 4, None),
        ("enroll_intent", 5, None),
    )

    recommendation, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    categories = {item.product.category for item in recommendation.items}
    assert "Data Engineering" in categories


def test_weak_retrieval_triggers_the_refine_loop(db, learner, catalog, fake_mesh):
    """The agent grades its own retrieval and searches again when it is bad."""
    fake_mesh.fail_grading_times = 1
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    recommendation, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    run = db.scalar(select(AgentRun))
    assert "refine_queries" in run.node_path
    assert run.node_path.count("retrieve") == 2, "it retrieved again after refining"
    assert run.retrieval_attempts == 2
    assert recommendation is not None, "it recovered rather than giving up"


def test_retrieval_budget_is_bounded(db, learner, catalog, fake_mesh):
    """Persistently bad grades must not loop forever."""
    fake_mesh.fail_grading_times = 99
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    recommendation, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    run = db.scalar(select(AgentRun))
    assert run.retrieval_attempts <= 3
    # It still produced something, from the best of a bad set.
    assert recommendation is not None


def test_hallucinated_product_ids_are_dropped(db, learner, catalog, fake_mesh):
    """The grounding guard: an invented course never reaches the database."""
    real_id = catalog[0].id
    fake_mesh.set_response(
        RecommendationDraft,
        RecommendationDraft(
            headline="Mixed real and invented",
            narrative="Two of these do not exist.",
            cta="Go",
            items=[
                RecommendedItem(product_id=real_id, why_this="This one is real."),
                RecommendedItem(product_id=999999, why_this="This course was invented."),
                RecommendedItem(product_id=888888, why_this="So was this one."),
            ],
        ),
    )
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    recommendation, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    stored_ids = {item.product_id for item in recommendation.items}
    assert 999999 not in stored_ids
    assert 888888 not in stored_ids
    assert real_id in stored_ids
    assert stored_ids <= {p.id for p in catalog}


def test_internal_ids_never_reach_user_facing_copy(db, learner, catalog, fake_mesh):
    """The candidate list shows the model "[17] Title"; readers must never see that."""
    real_id = catalog[0].id
    fake_mesh.set_response(
        RecommendationDraft,
        RecommendationDraft(
            headline=f"Start with [{real_id}] today",
            narrative=f"Begin with [{real_id}], then bring in [999999] for depth.",
            cta=f"Open [{real_id}]",
            items=[RecommendedItem(product_id=real_id, why_this=f"Follows on from [{real_id}].")],
        ),
    )
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    recommendation, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    copy = " ".join(
        [recommendation.headline, recommendation.narrative, recommendation.cta]
        + [i.why_this for i in recommendation.items]
    )
    assert "[" not in copy and "]" not in copy, copy
    # A known id is replaced by its title rather than simply deleted.
    assert catalog[0].title in recommendation.headline
    assert "999999" not in copy


def test_a_new_version_retires_the_previous_one(db, learner, catalog, fake_redis):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    first, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    # Simulate the cooldown elapsing: clear the Redis key *and* age the stored
    # recommendation, since the cooldown is enforced from both.
    fake_redis.flushall()
    first.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()

    add_events(
        db, learner, catalog,
        ("search", None, "kafka streaming"),
        ("product_view", 4, None),
        ("enroll_intent", 4, None),
    )
    second, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    assert second.version == 2
    assert second.is_current is True

    db.refresh(first)
    assert first.is_current is False

    current = db.scalars(
        select(Recommendation).where(Recommendation.is_current.is_(True))
    ).all()
    assert len(current) == 1


def test_below_threshold_activity_makes_no_llm_calls(db, learner, catalog, fake_mesh):
    """The efficiency claim, measured."""
    add_events(
        db, learner, catalog,
        ("page_view", None, None),
        ("page_view", None, None),
        ("scroll_depth", None, None),
        ("product_view", 0, None),
    )

    recommendation, decision = generate_recommendation(db, Audience(user_id=learner.id))

    assert recommendation is None
    assert decision.reason == "below_threshold"
    assert fake_mesh.call_count == 0, "not one model call for idle browsing"


def test_repeat_generation_is_short_circuited(db, learner, catalog, fake_mesh):
    """Same behaviour twice costs one agent run, not two."""
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()
    calls_after_first = fake_mesh.call_count

    second, decision = generate_recommendation(db, Audience(user_id=learner.id))

    assert second is None
    assert decision.reason in {"signature_unchanged", "cooldown"}
    assert fake_mesh.call_count == calls_after_first, "no extra Mesh calls"


def test_agent_failure_is_recorded_and_the_lock_released(db, learner, catalog, monkeypatch, fake_redis):
    from app.cache import reco_lock_key

    def boom(_state):
        raise RuntimeError("mesh exploded")

    monkeypatch.setattr("app.service.run_agent", boom)
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    try:
        generate_recommendation(db, Audience(user_id=learner.id))
    except RuntimeError:
        pass
    db.commit()

    run = db.scalar(select(AgentRun))
    assert run.status == "error"
    assert "mesh exploded" in run.error
    assert not fake_redis.exists(reco_lock_key(learner.id)), "lock must not leak"


# --- the routing decision in isolation --------------------------------------

def test_routing_generates_when_enough_candidates_survive():
    state = {"graded": [{}, {}, {}], "attempts": 1}
    assert route_after_grading(state) == "generate"


def test_routing_refines_when_results_are_thin():
    state = {"graded": [{}], "attempts": 1}
    assert route_after_grading(state) == "refine_queries"


def test_routing_stops_refining_when_the_budget_runs_out():
    state = {"graded": [], "attempts": 3}
    assert route_after_grading(state) == "generate"
