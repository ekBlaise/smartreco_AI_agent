"""Relational schema.

Five related concerns:
  users -> events (what they did)
        -> recommendations -> recommendation_items (what the agent decided)
  products <- recommendation_items, events   (the catalog, dual-written to Qdrant)
  agent_runs  (observability record of every agent invocation)
"""

from __future__ import annotations

import enum
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Role(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class EventType(str, enum.Enum):
    """Behavioural signals the frontend emits."""

    PAGE_VIEW = "page_view"
    PRODUCT_VIEW = "product_view"
    PRODUCT_CLICK = "product_click"
    SEARCH = "search"
    CATEGORY_FILTER = "category_filter"
    SCROLL_DEPTH = "scroll_depth"
    DWELL = "dwell"
    ENROLL_INTENT = "enroll_intent"
    RECO_IMPRESSION = "reco_impression"
    RECO_CLICK = "reco_click"


#: Weight each event contributes to the "is it worth calling the LLM yet?" score.
EVENT_WEIGHTS: dict[str, int] = {
    EventType.PAGE_VIEW.value: 0,
    EventType.PRODUCT_VIEW.value: 1,
    EventType.PRODUCT_CLICK.value: 2,
    EventType.SEARCH.value: 3,
    EventType.CATEGORY_FILTER.value: 2,
    EventType.SCROLL_DEPTH.value: 0,
    EventType.DWELL.value: 1,
    EventType.ENROLL_INTENT.value: 5,
    EventType.RECO_IMPRESSION.value: 0,
    EventType.RECO_CLICK.value: 2,
}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.USER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    events: Mapped[list["Event"]] = relationship(back_populates="user")
    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="user", order_by="Recommendation.created_at.desc()"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN.value


class Product(Base):
    """A course in the catalog.

    The dual-write to Qdrant is *verifiable* by content, not by timestamps: the
    row records a hash of the text it last embedded (``embedding_hash``) and a
    hash of the metadata it last pushed (``payload_hash``). Comparing those to
    the row's current content answers "is Qdrant up to date?" exactly, and lets
    the sync distinguish a change that needs a new embedding (description edited)
    from one that only needs a payload update (price changed) — the second is
    free, the first is not.

    Timestamps were the obvious design and the wrong one: writing
    ``vector_synced_at`` bumps ``updated_at`` via ``onupdate``, so a row would
    invalidate itself the instant it was marked clean.
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), index=True)
    level: Mapped[str] = mapped_column(String(40), default="beginner", index=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    instructor: Mapped[str] = mapped_column(String(120), default="")
    duration_hours: Mapped[float] = mapped_column(Float, default=0)
    rating: Mapped[float] = mapped_column(Float, default=0)
    enrollments: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # --- vector-store sync bookkeeping ------------------------------------
    embedding_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    payload_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    vector_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    vector_sync_error: Mapped[str | None] = mapped_column(Text, default=None)

    @property
    def embedding_document(self) -> str:
        """The text we embed.

        Title and category lead deliberately: they carry the strongest topical
        signal, and the retrieval queries the agent writes look like them.
        """
        tags = ", ".join(str(t) for t in (self.tags or []))
        parts = [
            f"Course: {self.title}",
            f"Category: {self.category}",
            f"Level: {self.level}",
        ]
        if tags:
            parts.append(f"Topics: {tags}")
        if self.instructor:
            parts.append(f"Taught by: {self.instructor}")
        parts.extend(["", self.description or ""])
        return "\n".join(parts).strip()

    @property
    def vector_payload(self) -> dict:
        """Metadata stored alongside the vector.

        Powers metadata filtering, and lets the grading node see candidates
        without a second database round-trip.
        """
        return {
            "product_id": self.id,
            "slug": self.slug,
            "title": self.title,
            "category": self.category,
            "level": self.level,
            "price": float(self.price or 0),
            "currency": self.currency,
            "instructor": self.instructor,
            "duration_hours": float(self.duration_hours or 0),
            "rating": float(self.rating or 0),
            "tags": [str(t) for t in (self.tags or [])],
            "is_active": bool(self.is_active),
            "summary": (self.description or "")[:400],
        }

    @property
    def content_digest(self) -> str:
        return _sha256(self.embedding_document)

    @property
    def payload_digest(self) -> str:
        return _sha256(json.dumps(self.vector_payload, sort_keys=True, default=str))

    @property
    def needs_embedding(self) -> bool:
        """The embedded text changed — a new vector must be computed."""
        return self.embedding_hash != self.content_digest

    @property
    def vector_in_sync(self) -> bool:
        return (
            self.vector_sync_error is None
            and not self.needs_embedding
            and self.payload_hash == self.payload_digest
        )


class Event(Base):
    """One tracked behavioural signal. Written in bulk from the Redis buffer."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_user_time", "user_id", "occurred_at"),
        Index("ix_events_session_time", "session_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), default=None, index=True
    )
    session_id: Mapped[str] = mapped_column(String(64), default="")
    type: Mapped[str] = mapped_column(String(40), index=True)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), default=None
    )
    query: Mapped[str | None] = mapped_column(String(300), default=None)
    path: Mapped[str | None] = mapped_column(String(300), default=None)
    dwell_ms: Mapped[int] = mapped_column(Integer, default=0)
    weight: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User | None"] = relationship(back_populates="events")
    product: Mapped["Product | None"] = relationship()


class Recommendation(Base):
    """A stored, agent-generated recommendation set for one user."""

    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("user_id", "version", name="uq_reco_user_version"),
        Index("ix_reco_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)

    headline: Mapped[str] = mapped_column(String(200))
    narrative: Mapped[str] = mapped_column(Text)
    cta: Mapped[str] = mapped_column(String(160), default="")

    # Snapshot of the behaviour that produced this recommendation.
    signature_hash: Mapped[str] = mapped_column(String(64), index=True)
    interests: Mapped[list] = mapped_column(JSON, default=list)
    trigger: Mapped[str] = mapped_column(String(40), default="behavior")

    model: Mapped[str] = mapped_column(String(80), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    trace_url: Mapped[str | None] = mapped_column(String(400), default=None)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="recommendations")
    items: Mapped[list["RecommendationItem"]] = relationship(
        back_populates="recommendation",
        cascade="all, delete-orphan",
        order_by="RecommendationItem.rank",
    )


class RecommendationItem(Base):
    """One recommended product plus the agent's persuasion for it."""

    __tablename__ = "recommendation_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("recommendations.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    why_this: Mapped[str] = mapped_column(Text, default="")
    relevance_score: Mapped[float] = mapped_column(Float, default=0)

    recommendation: Mapped["Recommendation"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()


class AgentRun(Base):
    """Observability record: one row per agent invocation, traced or not."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), default=None, index=True
    )
    trigger: Mapped[str] = mapped_column(String(40), default="behavior")
    status: Mapped[str] = mapped_column(String(20), default="ok", index=True)
    node_path: Mapped[list] = mapped_column(JSON, default=list)
    retrieval_attempts: Mapped[int] = mapped_column(Integer, default=0)
    candidates: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    events_considered: Mapped[int] = mapped_column(Integer, default=0)
    served_from_cache: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    recommendation_id: Mapped[int | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL"), default=None
    )
    trace_url: Mapped[str | None] = mapped_column(String(400), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User | None"] = relationship()
