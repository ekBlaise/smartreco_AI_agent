"""Recommendation service: trigger -> agent -> persistence.

Shared by the HTTP layer and the Celery workers so that a recommendation is
produced exactly one way regardless of what asked for it.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from app import realtime
from app.agent.graph import run_agent
from app.audience import Audience
from app.config import settings
from app.ingest import triggers
from app.ingest.triggers import BehaviorProfile, TriggerDecision
from app.llm.mesh import (
    MeshNotConfigured,
    is_billing_error,
    mark_unavailable,
    unavailable_reason,
)
from app.models import (
    AgentRun,
    Enrollment,
    Event,
    Product,
    Recommendation,
    RecommendationItem,
    User,
)

logger = logging.getLogger(__name__)


# --- behaviour -> agent input ----------------------------------------------

def behavior_payload(session: Session, profile: BehaviorProfile) -> dict[str, Any]:
    """Render the deterministic behaviour profile into what the agent reads."""
    titles: list[str] = []
    if profile.viewed_products:
        rows = session.scalars(
            select(Product).where(Product.id.in_(profile.viewed_products))
        ).all()
        by_id = {p.id: p for p in rows}
        titles = [by_id[pid].title for pid in profile.viewed_products if pid in by_id][:10]

    type_counts: dict[str, int] = {}
    for event in profile.events:
        type_counts[event.type] = type_counts.get(event.type, 0) + 1

    lines = [
        f"{count} x {event_type.replace('_', ' ')}"
        for event_type, count in sorted(type_counts.items(), key=lambda kv: -kv[1])
    ]
    summary = "; ".join(lines) or "no recorded activity"
    if profile.categories:
        top = ", ".join(f"{cat} ({n})" for cat, n in profile.categories[:4])
        summary += f". Category engagement: {top}"
    if profile.dominant_level:
        summary += f". Mostly {profile.dominant_level}-level material"

    return {
        "summary": summary,
        "searches": profile.searches,
        "top_categories": profile.top_categories,
        "top_terms": profile.top_terms,
        "viewed_titles": titles,
        "viewed_product_ids": profile.viewed_products,
        "total_dwell_ms": profile.total_dwell_ms,
        "event_count": profile.event_count,
        "score": profile.score,
    }


# --- persistence ------------------------------------------------------------

def owns_enrollment(audience: Audience):
    if audience.user_id is not None:
        return Enrollment.user_id == audience.user_id
    return Enrollment.session_id == audience.session_id


def enrolled_product_ids(session: Session, audience: Audience) -> list[int]:
    """Courses this audience already owns — excluded from every recommendation."""
    return list(session.scalars(select(Enrollment.product_id).where(owns_enrollment(audience))))


def _next_version(session: Session, audience: Audience) -> int:
    current = session.scalar(
        select(func.max(Recommendation.version)).where(triggers.owns(audience))
    )
    return int(current or 0) + 1


def persist_recommendation(
    session: Session,
    audience: Audience,
    profile: BehaviorProfile,
    result: dict[str, Any],
    *,
    trigger: str,
    latency_ms: int,
    llm_calls: int,
    trace_url: str | None,
) -> Recommendation:
    """Store the new recommendation and retire the previous one."""
    session.execute(
        update(Recommendation)
        .where(triggers.owns(audience), Recommendation.is_current.is_(True))
        .values(is_current=False)
    )

    recommendation = Recommendation(
        user_id=audience.user_id,
        session_id=None if audience.user_id else audience.session_id,
        version=_next_version(session, audience),
        headline=result.get("headline", ""),
        narrative=result.get("narrative", ""),
        cta=result.get("cta", ""),
        signature_hash=profile.signature,
        interests=profile.top_categories,
        trigger=trigger,
        model=settings.mesh_chat_model,
        latency_ms=latency_ms,
        llm_calls=llm_calls,
        trace_url=trace_url,
        is_current=True,
    )
    session.add(recommendation)
    session.flush()

    valid_ids = set(
        session.scalars(
            select(Product.id).where(
                Product.id.in_([i["product_id"] for i in result.get("items", [])])
            )
        )
    )
    rank = 0
    for item in result.get("items", []):
        if item["product_id"] not in valid_ids:
            # Belt and braces: the agent already filtered, but the catalog may
            # have changed between retrieval and write.
            logger.warning("Skipping product %s — no longer in catalog", item["product_id"])
            continue
        session.add(
            RecommendationItem(
                recommendation_id=recommendation.id,
                product_id=item["product_id"],
                rank=rank,
                why_this=item.get("why_this", ""),
                relevance_score=float(item.get("relevance_score", 0)),
            )
        )
        rank += 1
    session.flush()
    return recommendation


def _on_commit(session: Session, action) -> None:
    """Run ``action`` once, after this session's next successful commit.

    Used for side effects that live outside the database (Redis writes) and
    must not take effect if the transaction is rolled back.
    """

    @event.listens_for(session, "after_commit", once=True)
    def _run(_session) -> None:  # pragma: no cover - trivial delegation
        try:
            action()
        except Exception:
            logger.warning("Post-commit side effect failed", exc_info=True)


def _trace_url(run_id: str | None) -> str | None:
    if not (run_id and settings.langsmith_tracing and settings.langsmith_api_key):
        return None
    try:
        from langsmith import Client

        return Client().get_run_url(run_id=run_id, project_name=settings.langsmith_project)
    except Exception:  # pragma: no cover - tracing is best-effort
        return None


# --- the main entry point ---------------------------------------------------

def generate_recommendation(
    session: Session,
    audience: Audience,
    *,
    trigger: str = "behavior",
    force: bool = False,
) -> tuple[Recommendation | None, TriggerDecision]:
    """Evaluate the triggers and, if they pass, run the agent and store the result.

    Returns ``(recommendation_or_None, decision)`` — the decision explains *why*
    nothing was generated, which the dashboard and the tests both rely on.
    """
    decision = triggers.evaluate(session, audience, trigger=trigger, force=force)
    if not decision.should_generate:
        logger.info("Skipping generation for %s: %s", audience.key, decision.reason)
        # A tab may already be showing "thinking" because the tracking endpoint
        # queued this run optimistically. Stand it back down.
        realtime.publish_agent_state(audience.key, "idle", decision.reason)
        return None, decision

    started = time.perf_counter()
    run = AgentRun(
        user_id=audience.user_id,
        trigger=trigger,
        events_considered=decision.profile.event_count,
    )

    try:
        state = {
            "user_id": audience.user_id,
            "audience": audience.key,
            "trigger": trigger,
            "behavior": behavior_payload(session, decision.profile),
            # Never sell someone a course they already own. Recommending an
            # owned course is the classic failure that makes a recommender look
            # like it has not been paying attention at all.
            "exclude_product_ids": enrolled_product_ids(session, audience),
            "attempts": 0,
            "llm_calls": 0,
            "node_path": [],
        }
        final, run_id = _invoke_traced(state)

        latency_ms = int((time.perf_counter() - started) * 1000)
        run.node_path = final.get("node_path", [])
        run.retrieval_attempts = final.get("attempts", 0)
        run.candidates = len(final.get("candidates") or [])
        run.llm_calls = final.get("llm_calls", 0)
        run.latency_ms = latency_ms
        run.trace_url = _trace_url(run_id)

        if final.get("error") or not (final.get("result") or {}).get("items"):
            run.status = "empty"
            run.error = final.get("error") or "agent produced no groundable items"
            session.add(run)
            realtime.publish_agent_state(audience.key, "idle", "no_groundable_items")
            logger.warning("Agent returned nothing for %s: %s", audience.key, run.error)
            return None, decision

        recommendation = persist_recommendation(
            session,
            audience,
            decision.profile,
            final["result"],
            trigger=trigger,
            latency_ms=latency_ms,
            llm_calls=run.llm_calls,
            trace_url=run.trace_url,
        )
        run.status = "ok"
        run.recommendation_id = recommendation.id
        session.add(run)

        # Both of these are Redis writes, and Redis is not part of the database
        # transaction — so they wait for the commit. Starting the cooldown early
        # would leave a user blocked for ten minutes with nothing stored, and
        # pushing the recommendation early would show a tab something that then
        # vanished on rollback.
        payload = recommendation_payload(recommendation)
        _on_commit(session, lambda: triggers.mark_generated(audience, decision.profile.signature))
        _on_commit(session, lambda: realtime.publish(audience.key, {"type": "recommendation", **payload}))

        logger.info(
            "Generated recommendation v%d for %s in %dms (%d LLM calls, path=%s)",
            recommendation.version, audience.key, latency_ms, run.llm_calls,
            " -> ".join(run.node_path),
        )
        return recommendation, decision

    except MeshNotConfigured as exc:
        run.status = "unconfigured"
        run.error = str(exc)
        session.add(run)
        realtime.publish_agent_state(audience.key, "idle", "mesh_not_configured")
        logger.error("Cannot generate recommendations: %s", exc)
        return None, decision
    except Exception as exc:
        run.status = "error"
        run.error = f"{type(exc).__name__}: {exc}"[:2000]
        run.latency_ms = int((time.perf_counter() - started) * 1000)
        realtime.publish_agent_state(audience.key, "idle", "error")

        if is_billing_error(exc):
            # Retrying will fail identically until someone tops the account up.
            # Back off instead of burning a round-trip and a stack trace every
            # time the trigger fires — that noise buries real failures.
            run.status = "billing"
            run.error = "Mesh refused the call: insufficient balance"
            session.add(run)
            mark_unavailable(
                "Mesh has no balance — add credit to resume recommendations.",
                settings.mesh_backoff_seconds,
            )
            logger.error(
                "Mesh has no balance; pausing generation for %ss",
                settings.mesh_backoff_seconds,
            )
            return None, decision

        session.add(run)
        logger.exception("Agent run failed for %s", audience.key)
        raise
    finally:
        if decision.lock_key:
            triggers.clear_lock(audience)


def _invoke_traced(state: dict) -> tuple[dict, str | None]:
    """Run the agent, capturing the LangSmith run id when tracing is on."""
    if not (settings.langsmith_tracing and settings.langsmith_api_key):
        return run_agent(state), None
    try:
        from langchain_core.tracers.context import collect_runs

        with collect_runs() as cb:
            final = run_agent(state)
            run_id = str(cb.traced_runs[0].id) if cb.traced_runs else None
        return final, run_id
    except Exception:  # pragma: no cover - never let tracing break generation
        return run_agent(state), None


# --- read side --------------------------------------------------------------

def recommendation_payload(recommendation: Recommendation) -> dict[str, Any]:
    return {
        "id": recommendation.id,
        "version": recommendation.version,
        "headline": recommendation.headline,
        "narrative": recommendation.narrative,
        "cta": recommendation.cta,
        "interests": recommendation.interests or [],
        "trigger": recommendation.trigger,
        "created_at": recommendation.created_at,
        "items": [
            {
                "product_id": item.product_id,
                "slug": item.product.slug,
                "title": item.product.title,
                "category": item.product.category,
                "level": item.product.level,
                "price": float(item.product.price or 0),
                "currency": item.product.currency,
                "rating": float(item.product.rating or 0),
                "why_this": item.why_this,
                "relevance_score": item.relevance_score,
            }
            for item in recommendation.items
            if item.product is not None
        ],
    }


def recommendation_status(session: Session, audience: Audience) -> dict[str, Any]:
    """What the panel shows: a recommendation, or an honest reason why not."""
    recommendation = triggers.current_recommendation(session, audience)
    profile = triggers.build_profile(session, audience)

    if recommendation is not None:
        return {
            "status": "ready",
            "recommendation": recommendation_payload(recommendation),
            "events_tracked": profile.event_count,
            "behavior_score": profile.score,
            "score_needed": settings.reco_score_threshold,
            "message": "",
        }

    blocked = unavailable_reason()
    if blocked:
        # Say what is wrong instead of spinning "the agent is reading your
        # activity" forever against a call that cannot succeed.
        return {
            "status": "unavailable",
            "recommendation": None,
            "events_tracked": profile.event_count,
            "behavior_score": profile.score,
            "score_needed": settings.reco_score_threshold,
            "message": blocked,
        }

    if profile.event_count < settings.reco_min_events:
        remaining = settings.reco_min_events - profile.event_count
        return {
            "status": "insufficient_activity",
            "recommendation": None,
            "events_tracked": profile.event_count,
            "behavior_score": profile.score,
            "score_needed": settings.reco_score_threshold,
            "message": (
                f"Browse a few more courses — {remaining} more tracked action"
                f"{'s' if remaining != 1 else ''} and the agent has enough to work with."
            ),
        }

    # "The agent is reading your activity" was shown for every state that was
    # not `ready`, including the common one where the gates have looked at the
    # behaviour and decided it is not worth a model call yet. That reads as a
    # spinner that never resolves. Say which it actually is.
    short = settings.reco_score_threshold - profile.score
    if short > 0:
        return {
            "status": "insufficient_activity",
            "recommendation": None,
            "events_tracked": profile.event_count,
            "behavior_score": profile.score,
            "score_needed": settings.reco_score_threshold,
            "message": (
                f"{short} more point{'s' if short != 1 else ''} of interest signal and the "
                "agent has enough to write you a recommendation. Open a course, "
                "search for a topic, or spend a minute reading one."
            ),
        }

    return {
        "status": "pending",
        "recommendation": None,
        "events_tracked": profile.event_count,
        "behavior_score": profile.score,
        "score_needed": settings.reco_score_threshold,
        "message": (
            "Enough signal — the agent is writing your recommendation now. "
            "This updates on its own, usually within a minute."
        ),
    }


def users_with_recent_activity(session: Session, hours: int = 24) -> list[int]:
    """Users who did something in the window — the digest audience."""
    from datetime import timedelta

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = session.execute(
        select(Event.user_id, func.count(Event.id))
        .where(Event.user_id.is_not(None), Event.occurred_at >= since)
        .group_by(Event.user_id)
        .having(func.count(Event.id) >= settings.reco_min_events)
    ).all()
    return [int(user_id) for user_id, _ in rows]
