"""The gates that stop the agent running on every click.

These are the "efficient AI-call triggering" claims, verified.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.cache import reco_cooldown_key, reco_lock_key
from app.config import settings
from app.ingest import triggers
from app.models import Event, Recommendation


def add_events(db, user, catalog, *specs):
    """specs are (type, product_index_or_None, query_or_None) tuples."""
    from app.models import EVENT_WEIGHTS

    now = datetime.now(timezone.utc)
    for i, (event_type, product_idx, query) in enumerate(specs):
        db.add(
            Event(
                user_id=user.id,
                session_id="s1",
                type=event_type,
                product_id=catalog[product_idx].id if product_idx is not None else None,
                query=query,
                weight=EVENT_WEIGHTS.get(event_type, 0),
                occurred_at=now - timedelta(minutes=len(specs) - i),
            )
        )
    db.commit()


AGENTIC_SESSION = [
    ("product_view", 0, None),
    ("product_view", 1, None),
    ("search", None, "langgraph agents"),
    ("search", None, "multi agent orchestration"),
    ("product_click", 2, None),
    ("enroll_intent", 1, None),
]


def test_no_activity_means_no_llm_call(db, learner, catalog):
    decision = triggers.evaluate(db, learner.id)

    assert decision.should_generate is False
    assert decision.reason == "insufficient_activity"


def test_browsing_below_the_threshold_does_not_fire(db, learner, catalog):
    """Enough events to be interesting, not enough signal to be worth a call."""
    add_events(
        db, learner, catalog,
        ("page_view", None, None),
        ("page_view", None, None),
        ("scroll_depth", None, None),
        ("product_view", 0, None),
        ("scroll_depth", None, None),
    )

    decision = triggers.evaluate(db, learner.id)

    assert decision.should_generate is False
    assert decision.reason == "below_threshold"
    assert decision.profile.score < settings.reco_score_threshold


def test_real_intent_fires_the_agent(db, learner, catalog):
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    decision = triggers.evaluate(db, learner.id)

    assert decision.should_generate is True
    assert decision.reason == "score_threshold"
    assert decision.profile.score >= settings.reco_score_threshold
    assert decision.profile.top_categories[0] == "Agentic AI"


def test_the_profile_reflects_what_they_actually_did(db, learner, catalog):
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    profile = triggers.build_profile(db, learner.id)

    assert "langgraph agents" in profile.searches
    assert profile.dominant_level in {"intermediate", "advanced"}
    assert catalog[1].id in profile.viewed_products
    assert "langgraph" in profile.top_terms


def test_identical_behaviour_serves_the_cache_instead_of_regenerating(db, learner, catalog):
    """The hard short-circuit: same signature, zero LLM calls."""
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    profile = triggers.build_profile(db, learner.id)

    db.add(
        Recommendation(
            user_id=learner.id,
            version=1,
            headline="Existing",
            narrative="Already generated for this exact behaviour.",
            signature_hash=profile.signature,
            is_current=True,
        )
    )
    db.commit()

    decision = triggers.evaluate(db, learner.id)

    assert decision.should_generate is False
    assert decision.reason == "signature_unchanged"
    assert decision.serve_cached is True


def test_new_interests_change_the_signature_and_regenerate(db, learner, catalog, fake_redis):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    first = triggers.build_profile(db, learner.id)

    db.add(
        Recommendation(
            user_id=learner.id, version=1, headline="Old", narrative="",
            signature_hash=first.signature, is_current=True,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
    )
    db.commit()

    # They pivot to data engineering.
    add_events(
        db, learner, catalog,
        ("search", None, "kafka streaming pipelines"),
        ("product_view", 4, None),
        ("product_click", 4, None),
        ("enroll_intent", 5, None),
    )

    second = triggers.build_profile(db, learner.id)
    assert second.signature != first.signature

    decision = triggers.evaluate(db, learner.id)
    assert decision.should_generate is True
    assert decision.reason == "signature_changed"


def test_cooldown_blocks_a_rapid_second_run(db, learner, catalog, fake_redis):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    triggers.mark_generated(learner.id, "some-other-signature")

    decision = triggers.evaluate(db, learner.id)

    assert decision.should_generate is False
    assert decision.reason == "cooldown"
    assert fake_redis.exists(reco_cooldown_key(learner.id))


def test_an_in_flight_run_blocks_a_duplicate(db, learner, catalog, fake_redis):
    """Two batches arriving together must produce one agent run, not two."""
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    first = triggers.evaluate(db, learner.id)
    second = triggers.evaluate(db, learner.id)

    assert first.should_generate is True
    assert second.should_generate is False
    assert second.reason == "already_in_flight"

    triggers.clear_lock(learner.id)
    assert not fake_redis.exists(reco_lock_key(learner.id))

    third = triggers.evaluate(db, learner.id)
    assert third.should_generate is True


def test_force_bypasses_scoring_but_not_the_lock(db, learner, catalog):
    """A manual refresh should work even with thin activity."""
    add_events(db, learner, catalog, ("page_view", None, None))

    forced = triggers.evaluate(db, learner.id, force=True)
    assert forced.should_generate is True
    assert forced.reason == "forced"

    blocked = triggers.evaluate(db, learner.id, force=True)
    assert blocked.should_generate is False
    assert blocked.reason == "already_in_flight"


@pytest.mark.parametrize("window_hours,expected", [(72, 6), (0, 0)])
def test_only_recent_behaviour_counts(db, learner, catalog, monkeypatch, window_hours, expected):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    monkeypatch.setattr(settings, "reco_behavior_window_hours", window_hours)

    assert triggers.build_profile(db, learner.id).event_count == expected
