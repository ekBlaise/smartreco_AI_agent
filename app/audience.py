"""Who a recommendation is *for*.

Everything behavioural — the profile, the trigger gates, the cache keys, the
realtime channel, the stored recommendation — used to be keyed by ``user_id``,
which quietly meant none of it worked for a signed-out visitor. But anonymous
visitors are tracked exactly the same way (events carry a session id whether or
not anyone is logged in), so there was behaviour to reason about and nothing
would reason about it.

This is the one place that answers "whose behaviour is this?". A signed-in
person is keyed by their account, so their profile follows them between
browsers; everyone else is keyed by their session.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import User


@dataclass(frozen=True, slots=True)
class Audience:
    user_id: int | None = None
    session_id: str = ""

    @classmethod
    def of(cls, user: User | None, session_id: str = "") -> "Audience":
        return cls(user_id=user.id if user else None, session_id=session_id or "")

    @property
    def is_guest(self) -> bool:
        return self.user_id is None

    @property
    def key(self) -> str:
        """Stable cache/channel key. Prefixed so a user id and a session id can
        never collide."""
        return f"u:{self.user_id}" if self.user_id else f"s:{self.session_id}"

    @property
    def is_valid(self) -> bool:
        """A guest with no session cookie yet has nothing we can attribute."""
        return self.user_id is not None or bool(self.session_id)

    def __str__(self) -> str:  # pragma: no cover - logging convenience
        return self.key
