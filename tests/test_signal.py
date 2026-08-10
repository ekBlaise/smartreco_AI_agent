"""The live Signal panel: push channel, SSE stream, and first paint."""

from __future__ import annotations

from types import SimpleNamespace

import asyncio
import json

import pytest

from app import realtime
from app.api.routes import signal as signal_route
from app.api.templating import attr_json
from app.ingest.buffer import _normalize
from tests.test_triggers import AGENTIC_SESSION, add_events


# --- the push channel --------------------------------------------------------

def test_publish_reaches_a_subscriber_of_that_user(fake_redis):
    pubsub = fake_redis.pubsub()
    pubsub.subscribe(realtime.channel(7))
    pubsub.get_message(timeout=1)  # consume the subscribe confirmation

    assert realtime.publish(7, {"type": "recommendation", "version": 3}) is True

    message = pubsub.get_message(timeout=1)
    assert json.loads(message["data"]) == {"type": "recommendation", "version": 3}


def test_one_users_signal_never_lands_in_anothers_stream(fake_redis):
    pubsub = fake_redis.pubsub()
    pubsub.subscribe(realtime.channel(7))
    pubsub.get_message(timeout=1)

    realtime.publish(8, {"type": "recommendation", "version": 1})

    assert pubsub.get_message(timeout=0.2) is None


def test_publishing_without_redis_is_silent(monkeypatch):
    """Redis being down must cost a recommendation nothing but its liveness.

    Patch the lookup rather than calling ``cache.set_redis(None)`` — that only
    clears the cached client, so the next call would reconnect to a real Redis
    if one happened to be running locally.
    """
    monkeypatch.setattr(realtime, "get_redis", lambda: None)

    assert realtime.publish(1, {"type": "recommendation"}) is False
    assert realtime.publish_agent_state(1, "thinking") is False


# --- dwell accounting --------------------------------------------------------

def test_dwell_milestones_do_not_inflate_the_trigger_score():
    """Slicing a long read into milestones must not multiply its weight.

    Otherwise the live feed would quietly make the agent fire more often, which
    is the opposite of what the trigger gates are for.
    """
    milestone = _normalize(
        {"type": "dwell", "dwell_ms": 20_000, "meta": {"milestone": True, "cumulative_ms": 30_000}}
    )
    final = _normalize(
        {"type": "dwell", "dwell_ms": 5_000, "meta": {"milestone": False, "cumulative_ms": 35_000}}
    )

    assert milestone["weight"] == 0
    assert final["weight"] > 0
    # ...but every slice still counts toward how long they actually spent.
    assert milestone["dwell_ms"] == 20_000


def test_sustained_attention_is_scored_from_the_cumulative_total():
    """The final slice is small; the bonus must key off the whole session."""
    brief = _normalize({"type": "dwell", "dwell_ms": 3_000, "meta": {"cumulative_ms": 4_000}})
    sustained = _normalize({"type": "dwell", "dwell_ms": 3_000, "meta": {"cumulative_ms": 90_000}})

    assert sustained["weight"] > brief["weight"]


# --- attribute-safe JSON -----------------------------------------------------

@pytest.mark.parametrize(
    "hostile",
    ["Ada's Guide to Agents", 'He said "ship it"', "<script>alert(1)</script>", "a & b"],
)
def test_attr_json_cannot_break_out_of_a_single_quoted_attribute(hostile):
    rendered = str(attr_json({"title": hostile}))

    assert "'" not in rendered
    assert "<" not in rendered and ">" not in rendered
    # ' and friends are ordinary JSON escapes, so the value survives intact.
    assert json.loads(rendered)["title"] == hostile


def test_attr_json_survives_datetimes():
    """Recommendation payloads carry created_at; plain |tojson refuses it."""
    from datetime import datetime, timezone

    rendered = str(attr_json({"created_at": datetime(2026, 8, 9, tzinfo=timezone.utc)}))

    assert "2026-08-09" in rendered


# --- the SSE endpoint --------------------------------------------------------

class _StillConnected:
    """Minimal stand-in for Request — the endpoint only asks for these."""

    def __init__(self, session_id: str = "test-session"):
        self.state = SimpleNamespace(session_id=session_id)
        self.cookies: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return False


async def _first_frame(db, user, session_id: str = "test-session") -> str:
    """Open the real endpoint and take its opening frame, then shut it down."""
    response = await signal_route.stream(_StillConnected(session_id), db, user)
    frames = response.body_iterator
    try:
        async for chunk in frames:
            return chunk
    finally:
        # The stream is deliberately endless; closing it is how it ends.
        await frames.aclose()
    return ""


def test_a_guest_gets_a_stream_too(db, catalog):
    """The agent runs for signed-out visitors, so they get the same channel."""
    frame = asyncio.run(_first_frame(db, None, session_id="guest-session"))

    assert frame.startswith("event: snapshot")
    assert '"events_tracked"' in frame


def test_stream_opens_with_a_snapshot_of_current_state(db, learner, catalog):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()

    frame = asyncio.run(_first_frame(db, learner))

    assert frame.startswith("event: snapshot")
    snapshot = json.loads(frame.split("data: ", 1)[1])
    assert snapshot["events_tracked"] == len(AGENTIC_SESSION)
    assert "behavior_score" in snapshot


def test_a_published_recommendation_reaches_an_open_stream(fake_redis):
    """The worker publishes; a tab that is already listening must receive it."""

    async def listen_then_publish():
        received = []
        stream = realtime.subscribe(41)

        async def consume():
            async for payload in stream:
                if payload is not None:
                    received.append(payload)
                    return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.25)  # let the subscription settle
        realtime.publish(41, {"type": "recommendation", "version": 9})

        try:
            await asyncio.wait_for(task, timeout=5)
        finally:
            await stream.aclose()
        return received

    received = asyncio.run(listen_then_publish())

    assert received[0] == {"type": "recommendation", "version": 9}


# --- first paint -------------------------------------------------------------

def test_product_page_renders_the_panel_for_a_signed_in_user(logged_in, catalog):
    html = logged_in.get(f"/course/{catalog[0].slug}").text

    assert "Your Signal" in html
    assert "x-data='signalPanel(" in html
    # The panel must never be the reason a page fails to hydrate.
    assert 'signalPanel({"' not in html.replace("x-data='signalPanel(", "")


def test_anonymous_visitors_get_the_full_panel(client, catalog):
    """Guests are tracked identically, so they get the same panel, the same
    stream and the same agent — keyed by session instead of account."""
    html = client.get(f"/course/{catalog[0].slug}").text

    assert "Your Signal" in html
    assert "signal.js" in html
    assert '"owner": "s:' in html


def test_product_cards_expose_a_title_for_the_feed(logged_in, catalog):
    """Chips read titles from the DOM instead of shipping them to the server."""
    html = logged_in.get("/catalog").text

    assert f'data-track-title="{catalog[0].title}"' in html
