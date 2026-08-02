"""A fake Mesh chat model.

It presents the same interface the production code calls —
``chat_model(...).with_structured_output(Schema).invoke(messages)`` — and returns
a valid instance of whichever schema was asked for. Tests can override any
response, and can inspect ``calls`` to assert on how many model calls a code
path actually made (which is how the "don't call the LLM on every action" claims
are verified).
"""

from __future__ import annotations

import re
from typing import Any

from app.agent.state import (
    CandidateGrade,
    GradeBatch,
    InterestProfile,
    RecommendationDraft,
    RecommendedItem,
    RefinedQueries,
)


class _StructuredRunnable:
    def __init__(self, parent: "FakeChatModel", schema: type) -> None:
        self.parent = parent
        self.schema = schema

    def invoke(self, messages: Any, *args: Any, **kwargs: Any):
        return self.parent._respond(self.schema, messages)


class FakeChatModel:
    """Deterministic stand-in for a Mesh-backed ChatOpenAI."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.overrides: dict[type, Any] = {}
        #: force the grading node to reject everything on the first N passes,
        #: which is how the retrieval-refinement loop gets exercised.
        self.fail_grading_times = 0
        self._grade_calls = 0
        #: product ids the generator should claim to recommend; None = use candidates
        self.force_item_ids: list[int] | None = None

    # --- interface used by production code ---------------------------------

    def with_structured_output(self, schema: type, **_kwargs: Any) -> _StructuredRunnable:
        return _StructuredRunnable(self, schema)

    def invoke(self, messages: Any, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("SmartReco always uses structured output")

    # --- helpers ------------------------------------------------------------

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def calls_for(self, schema: type) -> int:
        return sum(1 for name, _ in self.calls if name == schema.__name__)

    def set_response(self, schema: type, value: Any) -> None:
        self.overrides[schema] = value

    # --- response generation ------------------------------------------------

    def _respond(self, schema: type, messages: Any):
        self.calls.append((schema.__name__, messages))

        if schema in self.overrides:
            return self.overrides[schema]

        text = self._flatten(messages)

        if schema is InterestProfile:
            return self._profile(text)
        if schema is GradeBatch:
            return self._grades(text)
        if schema is RefinedQueries:
            return RefinedQueries(
                queries=["agent orchestration production", "multi agent supervisor"],
                rationale="targeting the uncovered production angle",
            )
        if schema is RecommendationDraft:
            return self._draft(text)

        raise AssertionError(f"FakeChatModel has no response for {schema.__name__}")

    @staticmethod
    def _flatten(messages: Any) -> str:
        if isinstance(messages, str):
            return messages
        parts = []
        for message in messages or []:
            if isinstance(message, tuple):
                parts.append(str(message[1]))
            else:
                parts.append(str(getattr(message, "content", message)))
        return "\n".join(parts)

    @staticmethod
    def _candidate_ids(text: str) -> list[int]:
        """Read the product ids out of the rendered candidate block."""
        return [int(m) for m in re.findall(r"^\s*\[(\d+)\]", text, flags=re.M)]

    def _profile(self, text: str) -> InterestProfile:
        interests = ["agentic AI", "LangGraph"] if "Agentic AI" in text else ["data engineering"]
        return InterestProfile(
            interests=interests,
            skill_level="advanced" if "advanced" in text else "intermediate",
            intent="Ship a production agent",
            motivations=["wants to move from demo to production"],
            search_queries=["langgraph agents production", "multi agent orchestration"],
        )

    def _grades(self, text: str) -> GradeBatch:
        self._grade_calls += 1
        ids = self._candidate_ids(text)

        if self._grade_calls <= self.fail_grading_times:
            return GradeBatch(
                grades=[CandidateGrade(product_id=i, score=0.1, reason="off target") for i in ids],
                coverage_gap="nothing covers production deployment",
            )

        return GradeBatch(
            grades=[
                CandidateGrade(
                    product_id=pid,
                    # Rank by position so ordering assertions are meaningful.
                    score=max(0.6, 0.95 - 0.05 * rank),
                    reason="matches their demonstrated focus",
                )
                for rank, pid in enumerate(ids)
            ],
            coverage_gap="",
        )

    def _draft(self, text: str) -> RecommendationDraft:
        ids = self.force_item_ids if self.force_item_ids is not None else self._candidate_ids(text)[:3]
        return RecommendationDraft(
            headline="Your agent track, one step further",
            narrative=(
                "You have been circling LangGraph and multi-agent orchestration all week, "
                "and the gap between your prototypes and something you would put in front "
                "of users is production discipline. These pick up exactly there."
            ),
            cta="Start with the orchestration track",
            items=[
                RecommendedItem(product_id=pid, why_this=f"Continues the thread you started ({pid}).")
                for pid in ids
            ],
        )
