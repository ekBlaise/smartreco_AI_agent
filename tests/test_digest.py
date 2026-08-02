"""Scheduled proactive delivery: the daily digest and the Beat tasks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import settings
from app.models import Event, Product
from app.service import recommendation_payload, users_with_recent_activity
from app.workers.email import render_digest, send_digest
from tests.test_triggers import AGENTIC_SESSION, add_events


def _make_recommendation(db, learner, catalog):
    from app.service import generate_recommendation

    add_events(db, learner, catalog, *AGENTIC_SESSION)
    recommendation, _ = generate_recommendation(db, learner.id)
    db.commit()
    return recommendation


def test_digest_renders_with_real_recommendation_content(db, learner, catalog):
    recommendation = _make_recommendation(db, learner, catalog)

    subject, html = render_digest(learner, recommendation_payload(recommendation))

    assert subject == recommendation.headline
    assert recommendation.narrative in html
    for item in recommendation.items:
        assert item.product.title in html
        assert item.why_this in html
        assert f"/course/{item.product.slug}" in html


def test_digest_links_are_absolute(db, learner, catalog):
    recommendation = _make_recommendation(db, learner, catalog)

    _subject, html = render_digest(learner, recommendation_payload(recommendation))

    assert settings.public_base_url.rstrip("/") + "/course/" in html
    assert settings.public_base_url.rstrip("/") + "/dashboard" in html


def test_unconfigured_smtp_writes_a_dry_run_file(db, learner, catalog, tmp_path, monkeypatch):
    monkeypatch.setattr(type(settings), "digest_dir", property(lambda _self: tmp_path))
    recommendation = _make_recommendation(db, learner, catalog)
    subject, html = render_digest(learner, recommendation_payload(recommendation))

    result = send_digest(learner.email, subject, html)

    assert result["dry_run"] is True
    assert result["sent"] is False
    written = list(tmp_path.glob("*.html"))
    assert len(written) == 1
    content = written[0].read_text(encoding="utf-8")
    assert learner.email in content
    assert recommendation.headline in content


def test_digest_audience_is_only_the_recently_active(db, learner, admin, catalog):
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    # The admin browsed, but days ago.
    old = datetime.now(timezone.utc) - timedelta(days=5)
    for _ in range(6):
        db.add(Event(user_id=admin.id, session_id="s", type="product_view",
                     product_id=catalog[0].id, weight=1, occurred_at=old))
    db.commit()

    audience = users_with_recent_activity(db, hours=24)

    assert learner.id in audience
    assert admin.id not in audience


def test_a_quiet_user_gets_no_digest(db, admin, catalog):
    assert users_with_recent_activity(db, hours=24) == []


def test_daily_digest_task_runs_end_to_end(db, learner, catalog, tmp_path, monkeypatch):
    """The Beat task: generate, render, deliver — in dry-run mode."""
    monkeypatch.setattr(type(settings), "digest_dir", property(lambda _self: tmp_path))
    add_events(db, learner, catalog, *AGENTIC_SESSION)

    from app.workers.tasks import send_daily_digest

    result = send_daily_digest()

    assert result["enabled"] is True
    assert result["candidates"] == 1
    assert result["dry_run"] == 1
    assert result["failed"] == 0

    written = list(tmp_path.glob("*.html"))
    assert len(written) == 1

    # The delivered email contains real catalog courses, not placeholders.
    content = written[0].read_text(encoding="utf-8")
    assert learner.email in content
    assert any(product.title in content for product in catalog)


def test_digest_can_be_switched_off(db, learner, catalog, monkeypatch):
    monkeypatch.setattr(settings, "digest_enabled", False)

    from app.workers.tasks import send_daily_digest

    assert send_daily_digest() == {"enabled": False, "sent": 0}


# --- the other Beat tasks ---------------------------------------------------

def test_flush_task_drains_the_buffer(client, catalog, db):
    from app.workers.tasks import flush_event_buffer

    client.post(
        "/api/events/batch",
        json={"events": [
            {"type": "product_view", "product_id": catalog[0].id},
            {"type": "search", "query": "agents"},
        ]},
    )

    result = flush_event_buffer()

    assert result["flushed"] == 2
    assert db.scalar(select(func.count(Event.id))) == 2


def test_reconcile_task_repairs_a_stale_product(db, catalog):
    from app.vector import sync
    from app.workers.tasks import reconcile_vector_store

    product = catalog[0]
    product.embedding_hash = None
    product.payload_hash = None
    db.commit()

    assert len(sync.find_out_of_sync(db)) == 1

    result = reconcile_vector_store()

    assert result["embedded"] == 1
    assert result["pending"] == 0
    db.expire_all()
    assert db.get(Product, product.id).vector_in_sync


def test_reconcile_is_a_no_op_when_everything_is_synced(db, catalog, monkeypatch):
    from app.workers.tasks import reconcile_vector_store

    def boom(*_a, **_k):
        raise AssertionError("must not embed when nothing is stale")

    monkeypatch.setattr("app.vector.sync.embed_texts", boom)

    assert reconcile_vector_store() == {"embedded": 0, "skipped": 0, "failed": 0, "pending": 0}
