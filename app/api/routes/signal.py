"""Server-Sent Events stream behind the live "Your Signal" panel.

SSE rather than WebSockets on purpose: the traffic is one-directional (the
browser already has a perfectly good batched channel for going the other way),
it survives proxies as plain HTTP, and `EventSource` reconnects on its own.

If Redis is unreachable the stream closes immediately after the snapshot and the
panel falls back to polling ``/api/recommendations`` — the feature degrades to
"slightly less live" rather than breaking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import realtime
from app.api.deps import current_user_optional, get_session_id
from app.database import get_db
from app.audience import Audience
from app.models import User
from app.service import recommendation_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/signal", tags=["signal"])


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/stream")
async def stream(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
) -> StreamingResponse:
    """Live updates for one audience: agent state changes and new recommendations.

    Open to signed-out visitors, because the agent runs for them too.
    """
    audience = Audience.of(user, get_session_id(request))
    # Read the snapshot before the generator starts — the request-scoped session
    # is closed by the dependency teardown as soon as we return the response.
    snapshot = recommendation_status(db, audience)
    key = audience.key

    async def publisher() -> AsyncIterator[str]:
        yield _frame("snapshot", snapshot)

        last_beat = time.monotonic()
        try:
            async for payload in realtime.subscribe(key):
                if await request.is_disconnected():
                    break
                if payload is None:
                    if time.monotonic() - last_beat >= realtime.HEARTBEAT_SECONDS:
                        last_beat = time.monotonic()
                        yield ": keepalive\n\n"
                    continue
                last_beat = time.monotonic()
                yield _frame(payload.get("type", "message"), payload)
        except asyncio.CancelledError:  # pragma: no cover - client went away
            raise
        except Exception:
            logger.exception("Signal stream failed for %s", key)

        # Reached only when Redis is unavailable or the loop ended: tell the
        # client explicitly so it starts polling instead of silently going stale.
        yield _frame("closed", {"reason": "stream_ended"})

    return StreamingResponse(
        publisher(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx would otherwise buffer the stream
        },
    )
