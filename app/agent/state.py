"""Graph state and the structured shapes the LLM is asked to fill."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field


def _extend(left: list, right: list) -> list:
    """Reducer so nodes can append to node_path without clobbering it."""
    return (left or []) + (right or [])


def _add(left: int, right: int) -> int:
    return (left or 0) + (right or 0)


class RecoState(TypedDict, total=False):
    # --- inputs -------------------------------------------------------------
    user_id: int
    trigger: str
    behavior: dict[str, Any]      # deterministic summary from ingest.triggers

    # --- working state ------------------------------------------------------
    profile: dict[str, Any]       # LLM's reading of the behaviour
    queries: list[str]            # retrieval queries
    filters: dict[str, Any]       # Qdrant metadata filters
    candidates: list[dict]        # fused Qdrant hits
    graded: list[dict]            # candidates that survived relevance grading
    grade_feedback: str           # why the last retrieval was weak
    attempts: Annotated[int, _add]

    # --- output -------------------------------------------------------------
    result: dict[str, Any]        # headline / narrative / cta / items
    node_path: Annotated[list[str], _extend]
    llm_calls: Annotated[int, _add]
    error: str


# --- structured LLM outputs -------------------------------------------------

class InterestProfile(BaseModel):
    """What the agent concluded about the user from their raw activity."""

    interests: list[str] = Field(
        description="3-6 specific topics this user is pursuing, most important first",
        default_factory=list,
    )
    skill_level: str = Field(
        default="intermediate",
        description="beginner, intermediate or advanced, inferred from what they browse",
    )
    intent: str = Field(
        default="",
        description="One sentence on what they appear to be trying to accomplish",
    )
    motivations: list[str] = Field(
        default_factory=list,
        description="2-3 reasons this person would actually buy: career goal, gap, deadline",
    )
    search_queries: list[str] = Field(
        default_factory=list,
        description="2-4 catalog search queries that would surface courses they want",
    )


class CandidateGrade(BaseModel):
    product_id: int
    score: float = Field(ge=0, le=1, description="0-1 relevance to this user's interests")
    reason: str = Field(default="", description="Short justification")


class GradeBatch(BaseModel):
    grades: list[CandidateGrade] = Field(default_factory=list)
    coverage_gap: str = Field(
        default="",
        description="What the user wants that these results do NOT cover; empty if covered",
    )


class RefinedQueries(BaseModel):
    queries: list[str] = Field(default_factory=list, description="2-4 improved search queries")
    rationale: str = Field(default="", description="Why these should retrieve better")


class RecommendedItem(BaseModel):
    product_id: int = Field(description="Must be one of the supplied candidate IDs")
    why_this: str = Field(description="One persuasive sentence tying it to their behaviour")


class RecommendationDraft(BaseModel):
    headline: str = Field(description="Under 70 characters, specific to this user")
    narrative: str = Field(
        description="2-4 sentences referencing what they actually browsed and why these fit"
    )
    cta: str = Field(default="", description="Short call to action, under 60 characters")
    items: list[RecommendedItem] = Field(default_factory=list)
