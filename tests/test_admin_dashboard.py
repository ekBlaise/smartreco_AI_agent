"""The admin overview: health probes and the metrics it reports."""

from __future__ import annotations

import pytest

from app import health
from app.api import admin_metrics
from app.cache import SKIP_COUNTER_KEY, counters
from app.ingest import triggers
from app.service import generate_recommendation
from tests.test_triggers import AGENTIC_SESSION, add_events


# --- access ------------------------------------------------------------------

def test_learners_cannot_reach_the_dashboard(logged_in):
    response = logged_in.get("/admin", follow_redirects=False)

    assert response.status_code in (302, 303, 403)


def test_dashboard_renders_for_an_admin(admin_client):
    response = admin_client.get("/admin")

    assert response.status_code == 200
    assert "AI spend discipline" in response.text
    assert "Dual-write status" in response.text


def test_every_admin_page_carries_the_same_navigation(admin_client):
    for path in ("/admin", "/admin/products", "/admin/agent-runs"):
        html = admin_client.get(path).text
        assert 'href="/admin/agent-runs"' in html, path
        assert 'class="admin-tabs"' in html, path


# --- health probes -----------------------------------------------------------

def test_health_reports_each_dependency(db):
    components = health.snapshot(db)
    names = {c["name"] for c in components}

    assert {"PostgreSQL", "Qdrant", "Mesh API", "Redis", "Celery worker"} <= names
    assert all(c["status"] in {health.OK, health.DEGRADED, health.DOWN} for c in components)


def test_the_sqlite_fallback_is_reported_as_degraded_not_healthy(db):
    """Tests run on SQLite, which is exactly the situation worth flagging."""
    assert health.database(db)["status"] == health.DEGRADED


def test_a_down_essential_dependency_outranks_a_degraded_optional_one():
    essential_down = [
        {"name": "Qdrant", "status": health.DOWN, "essential": True, "detail": ""},
        {"name": "Redis", "status": health.OK, "essential": False, "detail": ""},
    ]
    optional_degraded = [
        {"name": "Qdrant", "status": health.OK, "essential": True, "detail": ""},
        {"name": "Redis", "status": health.DEGRADED, "essential": False, "detail": ""},
    ]

    assert health.worst(essential_down) == health.DOWN
    assert health.worst(optional_degraded) == health.DEGRADED


def test_health_never_raises_when_everything_is_unreachable(db, monkeypatch):
    """A broken dependency must render as a red card, not a 500 page."""
    monkeypatch.setattr(health, "get_redis", lambda: None)
    monkeypatch.setattr(health.store, "health", lambda: {"ok": False, "error": "refused"})

    components = health.snapshot(db)

    assert health.worst(components) in {health.DOWN, health.DEGRADED}


# --- the efficiency numbers --------------------------------------------------

def test_declined_generations_are_counted_by_reason(db, learner, catalog):
    """The gates' whole value is calls that never happened — so they get counted."""
    add_events(db, learner, catalog, *AGENTIC_SESSION[:1])  # below reco_min_events
    db.commit()

    triggers.evaluate(db, learner.id)
    triggers.evaluate(db, learner.id)

    assert counters(SKIP_COUNTER_KEY)["insufficient_activity"] == 2


def test_suppression_rate_compares_declines_against_real_runs(db, learner, catalog):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()
    generate_recommendation(db, learner.id)   # one real run
    db.commit()
    generate_recommendation(db, learner.id)   # declined: signature unchanged
    db.commit()

    metrics = admin_metrics.efficiency(db)

    assert metrics["runs"] == 1
    assert metrics["suppressed"] >= 1
    assert 0 < metrics["suppression_rate"] <= 100


def test_efficiency_reports_actions_per_run(db, learner, catalog):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()
    generate_recommendation(db, learner.id)
    db.commit()

    metrics = admin_metrics.efficiency(db)

    assert metrics["events_per_run"] == pytest.approx(len(AGENTIC_SESSION), abs=0.1)
    assert metrics["calls_per_run"] > 0


def test_metrics_are_safe_before_anything_has_happened(db):
    """A fresh install must render, not divide by zero."""
    metrics = admin_metrics.efficiency(db)

    assert metrics["runs"] == 0
    assert metrics["events_per_run"] is None
    assert metrics["suppression_rate"] is None
    assert admin_metrics.demand(db) == []


def test_ingest_metrics_count_events_and_buffer_depth(db, learner, catalog, fake_redis):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()
    fake_redis.rpush("smartreco:events:buffer", "{}", "{}")

    metrics = admin_metrics.ingest(db)

    assert metrics["total"] == len(AGENTIC_SESSION)
    assert metrics["buffered"] == 2
    assert dict(metrics["by_type"])


def test_demand_is_weighted_by_engagement_not_raw_event_count(db, learner, catalog):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()

    ranking = admin_metrics.demand(db)

    assert ranking
    assert all(weight > 0 for _, weight in ranking)
    assert ranking == sorted(ranking, key=lambda kv: -kv[1])


# --- agent runs page ---------------------------------------------------------

def test_agent_runs_page_can_be_filtered_by_status(admin_client, db, learner, catalog):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()
    generate_recommendation(db, learner.id)
    db.commit()

    assert "profile_behavior" in admin_client.get("/admin/agent-runs?status=ok").text
    empty = admin_client.get("/admin/agent-runs?status=error")
    assert "No runs with status" in empty.text


def test_a_run_shows_the_recommendation_it_produced(admin_client, db, learner, catalog):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()
    recommendation, _ = generate_recommendation(db, learner.id)
    db.commit()

    html = admin_client.get("/admin/agent-runs").text

    assert recommendation.headline in html
    assert recommendation.items[0].product.title in html
