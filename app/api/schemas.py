"""Pydantic request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import EventType

VALID_EVENT_TYPES = {e.value for e in EventType}


# --- events -----------------------------------------------------------------

class EventIn(BaseModel):
    """One tracked behavioural signal from the browser.

    Kept deliberately permissive: a malformed field must never cost the user a
    failed request, so unknown extras are dropped and bad rows are filtered
    server-side rather than raising.
    """

    model_config = ConfigDict(extra="ignore")

    type: str
    product_id: int | None = None
    query: str | None = Field(default=None, max_length=300)
    path: str | None = Field(default=None, max_length=300)
    dwell_ms: int = Field(default=0, ge=0, le=1000 * 60 * 60 * 6)
    occurred_at: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def known_type(cls, v: str) -> str:
        if v not in VALID_EVENT_TYPES:
            raise ValueError(f"unknown event type: {v}")
        return v


class EventBatchIn(BaseModel):
    events: list[EventIn] = Field(default_factory=list, max_length=50)


class EventBatchOut(BaseModel):
    accepted: int
    dropped: int
    buffered: bool


# --- products ---------------------------------------------------------------

class ProductIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=8000)
    category: str = Field(min_length=2, max_length=80)
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    price: float = Field(default=0, ge=0)
    currency: str = Field(default="USD", max_length=8)
    instructor: str = Field(default="", max_length=120)
    duration_hours: float = Field(default=0, ge=0)
    rating: float = Field(default=0, ge=0, le=5)
    tags: list[str] = Field(default_factory=list)
    is_active: bool = True


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    description: str
    category: str
    level: str
    price: float
    currency: str
    instructor: str
    duration_hours: float
    rating: float
    enrollments: int
    tags: list[str]
    is_active: bool
    vector_synced_at: datetime | None


# --- recommendations --------------------------------------------------------

class RecommendationItemOut(BaseModel):
    product_id: int
    slug: str
    title: str
    category: str
    level: str
    price: float
    currency: str
    rating: float
    why_this: str
    relevance_score: float


class RecommendationOut(BaseModel):
    id: int
    version: int
    headline: str
    narrative: str
    cta: str
    interests: list[str]
    trigger: str
    created_at: datetime
    items: list[RecommendationItemOut]


class RecommendationStatusOut(BaseModel):
    """What the dashboard polls: either a recommendation, or why there isn't one."""

    status: Literal["ready", "pending", "insufficient_activity", "disabled"]
    recommendation: RecommendationOut | None = None
    events_tracked: int = 0
    behavior_score: int = 0
    score_needed: int = 0
    message: str = ""


# --- auth -------------------------------------------------------------------

class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=120)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str
