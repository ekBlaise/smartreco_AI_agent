"""In-process fallback for draining the event buffer.

The buffer exists so tracking never blocks a request: events land in Redis and
Celery Beat bulk-inserts them a few seconds later. That is the intended path and
it is still the one that runs in production.

But it made the whole app *look broken* when someone ran only `uvicorn` — which
is how anyone tries a project for the first time. Events piled up in Redis,
Postgres stayed empty, and every screen that reads it ("actions tracked",
"interest signal", the agent's own behaviour profile) truthfully reported zero.
Nothing errored; it just silently did nothing.

So the API drains the buffer itself when nothing else is. A Redis lock means
only one process does it at a time, and because ``LPOP`` is atomic, Celery Beat
running at the same time is harmless — the two simply share the work.
"""

from __future__ import annotations

import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from app.cache import acquire_lock, get_redis, release_lock
from app.config import settings
from app.ingest import buffer

logger = logging.getLogger(__name__)

DRAIN_LOCK_KEY = "smartreco:events:drain-lock"


async def drain_once() -> dict[str, int]:
    """Flush one batch, if no other process is already doing it."""
    # Skip the lock entirely when Redis is gone: push_events falls back to
    # writing straight to Postgres, so there is nothing buffered to drain.
    if get_redis() is None:
        return {"flushed": 0, "invalid": 0, "remaining": 0}

    if not acquire_lock(DRAIN_LOCK_KEY, settings.event_drain_lock_seconds):
        return {"flushed": 0, "invalid": 0, "remaining": 0}
    try:
        # flush_buffer is blocking (Redis + a bulk INSERT), so it must not run
        # on the event loop or it would stall every concurrent request.
        return await run_in_threadpool(buffer.flush_buffer)
    finally:
        release_lock(DRAIN_LOCK_KEY)


async def run_forever() -> None:
    """Background loop started with the app; cancelled on shutdown."""
    logger.info(
        "Event drain running in-process every %ss (Celery Beat will share the work "
        "if it is also running)",
        settings.event_drain_interval_seconds,
    )
    while True:
        try:
            await asyncio.sleep(settings.event_drain_interval_seconds)
            result = await drain_once()
            if result["flushed"]:
                logger.debug("Drained %d buffered events", result["flushed"])
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient database or Redis blip must not kill the loop for the
            # remaining life of the process.
            logger.warning("Event drain iteration failed", exc_info=True)
