"""Anonymous-session middleware.

Every visitor gets a stable id cookie so their behaviour is attributable before
they sign in. Doing it in middleware keeps the cookie plumbing out of every
route handler — FastAPI does not merge a sub-response's headers when a handler
returns a Response directly, so per-route cookie setting would be repetitive and
easy to forget.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.api.deps import ANON_COOKIE
from app.config import settings


class AnonSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        session_id = request.cookies.get(ANON_COOKIE)
        is_new = not session_id
        if is_new:
            session_id = uuid.uuid4().hex
        request.state.session_id = session_id

        response = await call_next(request)

        if is_new:
            response.set_cookie(
                ANON_COOKIE,
                session_id,
                max_age=60 * 60 * 24 * 365,
                httponly=False,  # tracker.js reads it to tag events
                samesite="lax",
                secure=settings.public_base_url.startswith("https"),
            )
        return response
