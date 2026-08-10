"""Everything the admin dashboard reports.

Kept out of the route module because these are the numbers that make the
system's claims checkable — "tracking is cheap", "the agent does not run on
every click", "both stores stay in sync" — and they deserve to be readable and
testable on their own.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.cache import SKIP_COUNTER_KEY, counters, get_redis
from app.config import settings
from app.models import AgentRun, Event, Product, Recommendation, User

#: Reasons the trigger gates decline, in the order they are evaluated, with the
#: plain-English version an operator can act on.
SKIP_REASONS = {
    "insufficient_activity": "Too little activity to say anything",
    "below_threshold": "Not enough new signal yet",
    "signature_unchanged": "Interests unchanged — served the stored one",
    "cooldown": "Within the per-user cooldown",
    "already_in_flight": "A run was already going",
}


def _since(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def catalog(session: Session) -> dict[str, Any]:
    total, active = session.execute(
        select(
            func.count(Product.id),
            func.count(case((Product.is_active.is_(True), 1))),
        )
    ).one()
    by_category = session.execute(
        select(Product.category, func.count(Product.id))
        .group_by(Product.category)
        .order_by(func.count(Product.id).desc())
    ).all()
    return {
        "total": int(total or 0),
        "active": int(active or 0),
        "by_category": [(c, int(n)) for c, n in by_category],
    }


def ingest(session: Session) -> dict[str, Any]:
    """Event pipeline: volume, mix, and what is still sitting in the buffer."""
    total = int(session.scalar(select(func.count(Event.id))) or 0)
    last_24h = int(
        session.scalar(select(func.count(Event.id)).where(Event.occurred_at >= _since(24))) or 0
    )
    by_type = session.execute(
        select(Event.type, func.count(Event.id))
        .group_by(Event.type)
        .order_by(func.count(Event.id).desc())
    ).all()

    buffered = None
    client = get_redis()
    if client is not None:
        try:
            buffered = int(client.llen(settings.event_buffer_key))
        except Exception:
            buffered = None

    return {
        "total": total,
        "last_24h": last_24h,
        "by_type": [(t, int(n)) for t, n in by_type],
        "buffered": buffered,
        "tracked_users": int(
            session.scalar(select(func.count(func.distinct(Event.user_id)))) or 0
        ),
    }


def efficiency(session: Session) -> dict[str, Any]:
    """The judged claim: the agent does not run on every user action.

    Two numbers carry it — how many tracked actions each agent run represents,
    and how many generations the gates refused outright.
    """
    runs, llm_calls, avg_latency, cached = session.execute(
        select(
            func.count(AgentRun.id),
            func.coalesce(func.sum(AgentRun.llm_calls), 0),
            func.coalesce(func.avg(AgentRun.latency_ms), 0),
            func.count(case((AgentRun.served_from_cache.is_(True), 1))),
        )
    ).one()
    runs = int(runs or 0)
    llm_calls = int(llm_calls or 0)
    events = int(session.scalar(select(func.count(Event.id))) or 0)

    skips = counters(SKIP_COUNTER_KEY)
    suppressed = sum(skips.values())

    by_status = session.execute(
        select(AgentRun.status, func.count(AgentRun.id)).group_by(AgentRun.status)
    ).all()
    by_trigger = session.execute(
        select(AgentRun.trigger, func.count(AgentRun.id))
        .group_by(AgentRun.trigger)
        .order_by(func.count(AgentRun.id).desc())
    ).all()

    return {
        "runs": runs,
        "llm_calls": llm_calls,
        "avg_latency_ms": int(avg_latency or 0),
        "served_from_cache": int(cached or 0),
        "events_per_run": round(events / runs, 1) if runs else None,
        "calls_per_run": round(llm_calls / runs, 1) if runs else None,
        "suppressed": suppressed,
        # What share of *considered* generations we declined to pay for.
        "suppression_rate": (
            round(100 * suppressed / (suppressed + runs)) if (suppressed + runs) else None
        ),
        "skips": sorted(
            ((SKIP_REASONS.get(k, k), v) for k, v in skips.items()),
            key=lambda kv: -kv[1],
        ),
        "by_status": [(s, int(n)) for s, n in by_status],
        "by_trigger": [(t, int(n)) for t, n in by_trigger],
        "counters_available": bool(skips) or get_redis() is not None,
    }


def recommendations(session: Session) -> dict[str, Any]:
    total, current = session.execute(
        select(
            func.count(Recommendation.id),
            func.count(case((Recommendation.is_current.is_(True), 1))),
        )
    ).one()
    latest = list(
        session.scalars(
            select(Recommendation).order_by(Recommendation.created_at.desc()).limit(5)
        )
    )
    return {
        "total": int(total or 0),
        "current": int(current or 0),
        "users_with_one": int(current or 0),
        "latest": latest,
    }


def audience(session: Session) -> dict[str, Any]:
    total = int(session.scalar(select(func.count(User.id))) or 0)
    admins = int(session.scalar(select(func.count(User.id)).where(User.role == "admin")) or 0)
    active = int(
        session.scalar(
            select(func.count(func.distinct(Event.user_id))).where(
                Event.occurred_at >= _since(24), Event.user_id.is_not(None)
            )
        )
        or 0
    )
    return {"total": total, "admins": admins, "learners": total - admins, "active_24h": active}


def demand(session: Session, limit: int = 6) -> list[tuple[str, int]]:
    """Which categories learners actually engage with, weighted by event weight."""
    rows = session.execute(
        select(Product.category, func.coalesce(func.sum(Event.weight), 0))
        .join(Event, Event.product_id == Product.id)
        .group_by(Product.category)
        .order_by(func.coalesce(func.sum(Event.weight), 0).desc())
        .limit(limit)
    ).all()
    return [(c, int(n)) for c, n in rows if n]


def recent_runs(session: Session, limit: int = 6) -> list[AgentRun]:
    return list(
        session.scalars(select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit))
    )
