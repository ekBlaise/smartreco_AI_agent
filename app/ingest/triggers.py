"""When is it worth calling the LLM?

This module is pure Python — it makes **zero** AI calls. Its whole job is to
stop the agent from running on every click. Four independent gates must all pass
before a generation is queued:

1. **Volume**   — enough events to say anything meaningful (``reco_min_events``).
2. **Score**    — weighted behaviour score crosses a threshold, *or* the user's
                  interest signature materially changed since the last run.
3. **Cooldown** — at least ``reco_cooldown_seconds`` since the previous run.
4. **Lock**     — no generation already in flight for this user.

Plus a hard short-circuit: if the interest signature is byte-identical to the one
that produced the current recommendation, we serve the stored recommendation and
never touch Mesh at all.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache import (
    SKIP_COUNTER_KEY,
    acquire_lock,
    cache_get_json,
    cache_set_json,
    incr_counter,
    reco_cooldown_key,
    reco_lock_key,
    reco_signature_key,
    release_lock,
)
from app.audience import Audience
from app.config import settings
from app.models import Event, Product, Recommendation

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "how",
    "what", "best", "course", "courses", "learn", "learning", "tutorial", "guide",
}


@dataclass(slots=True)
class BehaviorProfile:
    """A cheap, deterministic read of what the user has been doing."""

    audience: Audience
    events: list[Event] = field(default_factory=list)
    score: int = 0
    categories: list[tuple[str, int]] = field(default_factory=list)
    levels: list[tuple[str, int]] = field(default_factory=list)
    terms: list[tuple[str, int]] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)
    viewed_products: list[int] = field(default_factory=list)
    total_dwell_ms: int = 0
    signature: str = ""

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def top_categories(self) -> list[str]:
        return [c for c, _ in self.categories[:3]]

    @property
    def top_terms(self) -> list[str]:
        return [t for t, _ in self.terms[:8]]

    @property
    def dominant_level(self) -> str | None:
        return self.levels[0][0] if self.levels else None


@dataclass(slots=True)
class TriggerDecision:
    should_generate: bool
    reason: str
    profile: BehaviorProfile
    serve_cached: bool = False
    lock_key: str | None = None


def _decline(reason: str, profile: BehaviorProfile, **kwargs) -> TriggerDecision:
    """Refuse to generate, and record why.

    Every one of these is an LLM call that did not happen. Counting them is the
    only way the efficiency of these gates is visible anywhere — a run that never
    happens leaves no log line, no agent_runs row, and no bill.
    """
    incr_counter(SKIP_COUNTER_KEY, reason)
    return TriggerDecision(False, reason, profile, **kwargs)


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9+#.]{3,}", (text or "").lower())
    return [w for w in words if w not in _STOPWORDS]


def _owned_by(audience: Audience):
    """Match this audience's events: their account, or their session."""
    if audience.user_id is not None:
        return Event.user_id == audience.user_id
    return Event.session_id == audience.session_id


def build_profile(session: Session, audience: Audience) -> BehaviorProfile:
    """Read this audience's recent behaviour and reduce it to a signature."""
    since = datetime.now(timezone.utc) - timedelta(hours=settings.reco_behavior_window_hours)
    events = list(
        session.scalars(
            select(Event)
            .where(_owned_by(audience), Event.occurred_at >= since)
            .order_by(Event.occurred_at.desc())
            .limit(400)
        )
    )

    profile = BehaviorProfile(audience=audience, events=events)
    if not events:
        profile.signature = _hash([])
        return profile

    product_ids = [e.product_id for e in events if e.product_id]
    products: dict[int, Product] = {}
    if product_ids:
        products = {
            p.id: p
            for p in session.scalars(select(Product).where(Product.id.in_(set(product_ids))))
        }

    category_counts: Counter[str] = Counter()
    level_counts: Counter[str] = Counter()
    term_counts: Counter[str] = Counter()
    searches: list[str] = []
    viewed: list[int] = []

    for event in events:
        profile.score += event.weight or 0
        profile.total_dwell_ms += event.dwell_ms or 0

        if event.query:
            searches.append(event.query.strip())
            for token in _tokenize(event.query):
                term_counts[token] += 3  # an explicit search is a loud signal

        meta = event.meta or {}
        if isinstance(meta, dict) and meta.get("category"):
            category_counts[str(meta["category"])] += 2

        product = products.get(event.product_id) if event.product_id else None
        if product is None:
            continue
        if event.type in {"product_view", "product_click", "enroll_intent", "reco_click"}:
            viewed.append(product.id)
        weight = 3 if event.type == "enroll_intent" else 1
        category_counts[product.category] += weight
        level_counts[product.level] += weight
        for tag in product.tags or []:
            term_counts[str(tag).lower()] += weight

    profile.categories = category_counts.most_common()
    profile.levels = level_counts.most_common()
    profile.terms = term_counts.most_common()
    profile.searches = list(dict.fromkeys(searches))[:10]
    profile.viewed_products = list(dict.fromkeys(viewed))[:20]
    profile.signature = _hash(
        [
            *sorted(profile.top_categories),
            *sorted(profile.top_terms[:6]),
            *sorted(str(p) for p in profile.viewed_products[:8]),
            profile.dominant_level or "",
        ]
    )
    return profile


def _hash(parts: list[str]) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def owns(audience: Audience):
    """Match recommendations belonging to this audience."""
    if audience.user_id is not None:
        return Recommendation.user_id == audience.user_id
    return Recommendation.session_id == audience.session_id


def current_recommendation(session: Session, audience: Audience) -> Recommendation | None:
    return session.scalar(
        select(Recommendation)
        .where(owns(audience), Recommendation.is_current.is_(True))
        .order_by(Recommendation.created_at.desc())
    )


def evaluate(
    session: Session,
    audience: Audience,
    trigger: str = "behavior",
    force: bool = False,
    acquire: bool = True,
) -> TriggerDecision:
    """Decide whether to spend an LLM call on this user right now.

    ``acquire=False`` evaluates the gates *without* taking the single-flight
    lock. The tracking endpoint uses that to pre-filter cheaply before queueing;
    the worker then re-evaluates with ``acquire=True`` and owns the lock for the
    duration of the run. If the endpoint took the lock itself, the task it just
    queued would be turned away by it.
    """
    profile = build_profile(session, audience)
    lock = reco_lock_key(audience.key)

    if force:
        if acquire and not acquire_lock(lock, settings.reco_lock_ttl_seconds):
            return _decline("already_in_flight", profile)
        return TriggerDecision(True, "forced", profile, lock_key=lock if acquire else None)

    # Gate 1 — do we know anything at all about this person?
    if profile.event_count < settings.reco_min_events:
        return _decline("insufficient_activity", profile)

    existing = current_recommendation(session, audience)
    cached_signature = cache_get_json(reco_signature_key(audience.key))
    last_signature = cached_signature or (existing.signature_hash if existing else None)

    # Hard short-circuit — nothing changed, so reuse what we already generated.
    if existing is not None and last_signature == profile.signature:
        return _decline("signature_unchanged", profile, serve_cached=True)

    # Gate 2 — is there enough new signal to justify the call?
    signature_changed = existing is not None and last_signature != profile.signature
    if profile.score < settings.reco_score_threshold and not signature_changed:
        return _decline("below_threshold", profile)

    # Gate 3 — don't regenerate more often than the cooldown allows.
    if cache_get_json(reco_cooldown_key(audience.key)):
        return _decline("cooldown", profile, serve_cached=existing is not None)
    if existing is not None and _within_cooldown(existing):
        return _decline("cooldown", profile, serve_cached=True)

    # Gate 4 — single-flight.
    if acquire and not acquire_lock(lock, settings.reco_lock_ttl_seconds):
        return _decline("already_in_flight", profile, serve_cached=existing is not None)

    reason = "signature_changed" if signature_changed else "score_threshold"
    logger.info(
        "Recommendation trigger fired for %s reason=%s score=%d events=%d",
        audience.key, reason, profile.score, profile.event_count,
    )
    return TriggerDecision(True, reason, profile, lock_key=lock if acquire else None)


def _within_cooldown(recommendation: Recommendation) -> bool:
    created = recommendation.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created).total_seconds()
    return age < settings.reco_cooldown_seconds


def mark_generated(audience: Audience, signature: str) -> None:
    """Start the cooldown and remember the signature we just generated for."""
    cache_set_json(
        reco_signature_key(audience.key), signature, settings.reco_behavior_window_hours * 3600
    )
    cache_set_json(reco_cooldown_key(audience.key), True, settings.reco_cooldown_seconds)


def clear_lock(audience: Audience) -> None:
    release_lock(reco_lock_key(audience.key))
