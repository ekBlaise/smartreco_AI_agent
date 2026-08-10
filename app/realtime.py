"""Server -> browser push for the live Signal panel.

The panel shows two different things, and they arrive by two different routes:

* **What the user just did** — rendered straight from the tracker's own queue in
  the browser. It needs no server round-trip, so a chip appears the instant the
  action happens even though the event itself leaves in a batch seconds later.
* **What the agent did about it** — that genuinely happens on the server, in a
  Celery worker, seconds to a minute later. It reaches the browser through this
  module: the worker publishes to a per-user Redis channel and the SSE endpoint
  in :mod:`app.api.routes.signal` relays it to whichever tabs are open.

Publishing is best-effort in exactly the same way as the rest of :mod:`app.cache`
— if Redis is down the recommendation is still generated and stored, the browser
just falls back to polling for it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.cache import get_redis
from app.config import settings

logger = logging.getLogger(__name__)

#: How long the SSE loop waits for a message before emitting a keepalive.
POLL_TIMEOUT_SECONDS = 1.0

#: Proxies commonly cut idle connections at 30-60s.
HEARTBEAT_SECONDS = 20


#: Overridable async-client factory. The sync side has app.cache.set_redis for
#: this; without a matching seam here, tests would silently talk to whatever
#: Redis happens to be listening on the developer's machine, and the suite would
#: pass or hang depending on whether a container was running.
_async_client_factory: Callable[[], Any] | None = None


def set_async_client_factory(factory: Callable[[], Any] | None) -> None:
    global _async_client_factory
    _async_client_factory = factory


def _async_client() -> Any:
    if _async_client_factory is not None:
        return _async_client_factory()
    import redis.asyncio as aioredis

    return aioredis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2.0,
    )


def channel(key: str) -> str:
    return f"smartreco:signal:{key}"


def publish(key: str, payload: dict[str, Any]) -> bool:
    """Push an update to any tabs this user has open. Never raises."""
    client = get_redis()
    if client is None:
        return False
    try:
        client.publish(channel(key), json.dumps(payload, default=str))
        return True
    except Exception:
        logger.debug("Signal publish failed for %s", key, exc_info=True)
        return False


def publish_agent_state(key: str, state: str, reason: str = "") -> bool:
    """Tell the panel the agent has started or finished thinking.

    ``state`` is one of ``thinking``, ``idle``. This is what turns the panel's
    status dot from "streaming" to "agent thinking" while a run is in flight.
    """
    return publish(key, {"type": "agent_state", "state": state, "reason": reason})


async def subscribe(key: str) -> AsyncIterator[dict[str, Any] | None]:
    """Yield published payloads for a user; yields ``None`` as a keepalive tick.

    Uses the asyncio Redis client rather than the shared sync one — this runs
    inside the event loop, and a blocking read here would stall every other
    request the web process is serving.
    """
    client = None
    pubsub = None
    try:
        client = _async_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(channel(key))
    except Exception:
        logger.info("Realtime unavailable; client will fall back to polling")
        if pubsub is not None:
            await _close(pubsub, client)
        elif client is not None:
            await client.aclose()
        return

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=POLL_TIMEOUT_SECONDS
            )
            if message is None:
                yield None  # keepalive tick; the route decides whether to write
                continue
            try:
                yield json.loads(message["data"])
            except (TypeError, ValueError):
                continue
    finally:
        await _close(pubsub, client)


async def _close(pubsub, client) -> None:
    for closer in (pubsub.unsubscribe(), pubsub.aclose(), client.aclose()):
        try:
            await closer
        except Exception:  # pragma: no cover - teardown is best-effort
            pass
