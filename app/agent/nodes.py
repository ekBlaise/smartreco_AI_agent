"""The agent's reasoning nodes.

Every LLM call here goes through :mod:`app.llm.mesh`, and every product that can
possibly be recommended comes out of Qdrant — the model is never allowed to
invent catalog entries.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

from app.agent.prompts import (
    GENERATE_SYSTEM,
    GENERATE_USER,
    GRADE_SYSTEM,
    GRADE_USER,
    PROFILE_SYSTEM,
    PROFILE_USER,
    REFINE_SYSTEM,
    REFINE_USER,
)
from app.agent.state import (
    GradeBatch,
    InterestProfile,
    RecoState,
    RecommendationDraft,
    RefinedQueries,
)
from app.config import settings
from app.llm.mesh import chat_model, embed_query
from app.vector import store

logger = logging.getLogger(__name__)

RRF_K = 60  # reciprocal-rank-fusion constant
MIN_ITEMS = 3  # below this a recommendation looks thin; above it, padding hurts


def _structured(schema, temperature: float = 0.3):
    return chat_model(temperature=temperature).with_structured_output(schema)


def _behavior(state: RecoState) -> dict[str, Any]:
    return state.get("behavior") or {}


def _profile(state: RecoState) -> dict[str, Any]:
    return state.get("profile") or {}


def _format_candidates(candidates: list[dict], include_summary: bool = True) -> str:
    lines = []
    for c in candidates:
        head = (
            f"[{c['product_id']}] {c['title']} — {c['category']} / {c['level']} / "
            f"{c['currency']} {c['price']:.0f} / rated {c['rating']}"
        )
        if include_summary and c.get("summary"):
            head += f"\n      {c['summary'][:240]}"
        lines.append(head)
    return "\n".join(lines) if lines else "(none)"


# --- 1. understand ----------------------------------------------------------

def profile_behavior(state: RecoState) -> RecoState:
    """Turn raw activity into a structured reading of the user's intent."""
    behavior = _behavior(state)
    prompt = PROFILE_USER.format(
        behavior_summary=behavior.get("summary", "(no activity)"),
        searches=", ".join(behavior.get("searches") or []) or "(none)",
        categories=", ".join(behavior.get("top_categories") or []) or "(none)",
        viewed_titles=", ".join(behavior.get("viewed_titles") or []) or "(none)",
        dwell_minutes=round((behavior.get("total_dwell_ms") or 0) / 60000, 1),
        event_count=behavior.get("event_count", 0),
    )
    profile: InterestProfile = _structured(InterestProfile).invoke(
        [("system", PROFILE_SYSTEM), ("user", prompt)]
    )
    return {
        "profile": profile.model_dump(),
        "node_path": ["profile_behavior"],
        "llm_calls": 1,
    }


# --- 2. plan (deterministic — no LLM call needed) ---------------------------

def plan_queries(state: RecoState) -> RecoState:
    """Build retrieval queries from the profile plus the raw behavioural terms.

    Deliberately not an LLM call: the profile node already produced query
    suggestions, and the behavioural terms are facts. Spending a fourth model
    call to restate them would be waste.
    """
    behavior = _behavior(state)
    profile = _profile(state)

    queries: list[str] = []
    queries.extend(q.strip() for q in profile.get("search_queries") or [] if q.strip())
    queries.extend(s for s in (behavior.get("searches") or [])[:2])

    interests = profile.get("interests") or []
    if interests:
        queries.append(", ".join(interests[:3]))
    if not queries and behavior.get("top_categories"):
        queries.append(" ".join(behavior["top_categories"][:2]))

    # De-duplicate case-insensitively, keep order, cap the fan-out.
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        key = q.lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(q)

    filters: dict[str, Any] = {}
    level = profile.get("skill_level")
    if level in {"beginner", "intermediate", "advanced"}:
        # A soft preference, applied as a *second* filtered pass in retrieve()
        # so it can never starve the unfiltered results.
        filters["preferred_levels"] = _adjacent_levels(level)

    return {
        "queries": unique[:4] or ["popular online courses"],
        "filters": filters,
        "node_path": ["plan_queries"],
    }


def _adjacent_levels(level: str) -> list[str]:
    order = ["beginner", "intermediate", "advanced"]
    idx = order.index(level)
    return order[max(0, idx - 1) : idx + 2]


# --- 3. retrieve ------------------------------------------------------------

def retrieve(state: RecoState) -> RecoState:
    """Semantic retrieval over Qdrant, fused across queries with RRF.

    Retrieval polish: multi-query fan-out, reciprocal rank fusion, a
    level-filtered second pass, and a small boost for catalog quality signals.
    """
    queries = state.get("queries") or []
    filters = state.get("filters") or {}
    preferred_levels = filters.get("preferred_levels")

    fused: dict[int, dict[str, Any]] = {}

    def absorb(hits, weight: float) -> None:
        for rank, hit in enumerate(hits):
            entry = fused.setdefault(
                hit.product_id,
                {
                    "product_id": hit.product_id,
                    "slug": hit.payload.get("slug", ""),
                    "title": hit.payload.get("title", ""),
                    "category": hit.payload.get("category", ""),
                    "level": hit.payload.get("level", ""),
                    "price": float(hit.payload.get("price", 0) or 0),
                    "currency": hit.payload.get("currency", "USD"),
                    "rating": float(hit.payload.get("rating", 0) or 0),
                    "summary": hit.payload.get("summary", ""),
                    "tags": hit.payload.get("tags", []),
                    "vector_score": hit.score,
                    "fusion_score": 0.0,
                },
            )
            entry["fusion_score"] += weight / (RRF_K + rank + 1)
            entry["vector_score"] = max(entry["vector_score"], hit.score)

    per_query = max(6, settings.reco_candidate_pool // max(1, len(queries)) + 4)

    for query in queries:
        vector = embed_query(query)
        absorb(store.search(vector, limit=per_query, query_filter=store.build_filter()), 1.0)
        if preferred_levels:
            absorb(
                store.search(
                    vector,
                    limit=per_query,
                    query_filter=store.build_filter(levels=preferred_levels),
                ),
                0.5,
            )

    # Drop owned courses before grading rather than after: they would otherwise
    # occupy slots in the candidate pool, get graded (an LLM call), and then be
    # thrown away — crowding out courses the learner could actually buy.
    excluded = set(state.get("exclude_product_ids") or [])
    candidates = sorted(
        (c for c in fused.values() if c["product_id"] not in excluded),
        # Tiny quality nudge so near-ties resolve toward well-rated courses.
        key=lambda c: c["fusion_score"] + (c["rating"] / 500),
        reverse=True,
    )[: settings.reco_candidate_pool]

    logger.info(
        "Retrieved %d candidates for user=%s across %d queries",
        len(candidates), state.get("user_id"), len(queries),
    )
    return {
        "candidates": candidates,
        "attempts": 1,
        "node_path": ["retrieve"],
    }


# --- 4. grade ---------------------------------------------------------------

def grade_candidates(state: RecoState) -> RecoState:
    """LLM re-ranking: keep only candidates that genuinely fit this user."""
    candidates = state.get("candidates") or []
    if not candidates:
        return {"graded": [], "grade_feedback": "no candidates retrieved", "node_path": ["grade_candidates"]}

    behavior = _behavior(state)
    profile = _profile(state)
    prompt = GRADE_USER.format(
        interests=", ".join(profile.get("interests") or []) or "(unknown)",
        skill_level=profile.get("skill_level", "intermediate"),
        intent=profile.get("intent", "(unknown)"),
        viewed_titles=", ".join(behavior.get("viewed_titles") or []) or "(none)",
        candidates=_format_candidates(candidates),
    )
    batch: GradeBatch = _structured(GradeBatch, temperature=0.0).invoke(
        [("system", GRADE_SYSTEM), ("user", prompt)]
    )

    scores = {g.product_id: g for g in batch.grades}
    graded = []
    for candidate in candidates:
        grade = scores.get(candidate["product_id"])
        if grade is None or grade.score < settings.reco_relevance_floor:
            continue
        graded.append({**candidate, "relevance_score": grade.score, "grade_reason": grade.reason})
    graded.sort(key=lambda c: c["relevance_score"], reverse=True)
    graded = _diversify(graded)

    logger.info(
        "Graded %d/%d candidates above %.2f",
        len(graded), len(candidates), settings.reco_relevance_floor,
    )
    return {
        "graded": graded,
        "grade_feedback": batch.coverage_gap,
        "node_path": ["grade_candidates"],
        "llm_calls": 1,
    }


#: How much a repeated category is penalised, per course already chosen from it.
DIVERSITY_PENALTY = 0.08


def _diversify(graded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-order so one category cannot monopolise the list (MMR-style).

    Semantic search is very good at returning five versions of the same course.
    Someone deep in agentic AI should still see the MLOps course that gets their
    agents deployed, so each additional pick from an already-used category is
    penalised. Relevance still dominates: the penalty only reorders genuine
    near-ties, and nothing is dropped.
    """
    remaining = list(graded)
    ordered: list[dict[str, Any]] = []
    seen: Counter[str] = Counter()

    while remaining:
        best = max(
            remaining,
            key=lambda c: c["relevance_score"] - DIVERSITY_PENALTY * seen[c.get("category", "")],
        )
        remaining.remove(best)
        ordered.append(best)
        seen[best.get("category", "")] += 1
    return ordered


# --- 5. refine (loop back to retrieve) --------------------------------------

def refine_queries(state: RecoState) -> RecoState:
    """Rewrite the queries when retrieval came back weak."""
    profile = _profile(state)
    graded = state.get("graded") or []
    refined: RefinedQueries = _structured(RefinedQueries, temperature=0.5).invoke(
        [
            ("system", REFINE_SYSTEM),
            (
                "user",
                REFINE_USER.format(
                    interests=", ".join(profile.get("interests") or []) or "(unknown)",
                    skill_level=profile.get("skill_level", "intermediate"),
                    intent=profile.get("intent", "(unknown)"),
                    queries=", ".join(state.get("queries") or []),
                    coverage_gap=state.get("grade_feedback") or "results were only loosely related",
                    best_titles=", ".join(c["title"] for c in graded[:3]) or "(nothing usable)",
                ),
            ),
        ]
    )
    queries = [q.strip() for q in refined.queries if q.strip()] or state.get("queries") or []
    logger.info("Refining retrieval: %s", queries)
    return {
        "queries": queries[:4],
        "node_path": ["refine_queries"],
        "llm_calls": 1,
    }


# --- 6. generate ------------------------------------------------------------

def generate(state: RecoState) -> RecoState:
    """Write the persuasive, behaviour-grounded recommendation."""
    graded = state.get("graded") or []
    pool = graded or (state.get("candidates") or [])
    pool = pool[: settings.reco_candidate_pool]

    if not pool:
        return {
            "error": "no catalog candidates available",
            "node_path": ["generate"],
        }

    behavior = _behavior(state)
    profile = _profile(state)
    prompt = GENERATE_USER.format(
        interests=", ".join(profile.get("interests") or []) or "(unknown)",
        skill_level=profile.get("skill_level", "intermediate"),
        intent=profile.get("intent", "(unknown)"),
        motivations="; ".join(profile.get("motivations") or []) or "(unknown)",
        behavior_summary=behavior.get("summary", "(no activity)"),
        searches=", ".join(behavior.get("searches") or []) or "(none)",
        viewed_titles=", ".join(behavior.get("viewed_titles") or []) or "(none)",
        candidates=_format_candidates(pool),
        max_items=settings.reco_max_items,
    )
    draft: RecommendationDraft = _structured(RecommendationDraft, temperature=0.7).invoke(
        [("system", GENERATE_SYSTEM), ("user", prompt)]
    )
    return {
        "result": draft.model_dump(),
        "node_path": ["generate"],
        "llm_calls": 1,
    }


# --- 7. finalize (grounding guard) ------------------------------------------

def finalize(state: RecoState) -> RecoState:
    """Drop anything the model invented and assemble the final payload.

    This is the grounding guard: a product_id that was not retrieved from Qdrant
    cannot survive to the database, no matter what the model returned.
    """
    result = state.get("result") or {}
    excluded = set(state.get("exclude_product_ids") or [])
    pool = {
        c["product_id"]: c
        for c in (state.get("graded") or [])
        if c["product_id"] not in excluded
    }
    for candidate in state.get("candidates") or []:
        if candidate["product_id"] not in excluded:
            pool.setdefault(candidate["product_id"], candidate)

    items: list[dict[str, Any]] = []
    hallucinated: list[int] = []
    owned: list[int] = []
    for entry in result.get("items") or []:
        pid = entry.get("product_id")
        if pid in excluded:
            # Retrieval already filtered these out, but the model can still name
            # one from the behaviour summary. This is the gate that counts.
            owned.append(pid)
            continue
        candidate = pool.get(pid)
        if candidate is None:
            hallucinated.append(pid)
            continue
        items.append(
            {
                "product_id": pid,
                "why_this": (entry.get("why_this") or "").strip(),
                "relevance_score": float(candidate.get("relevance_score", candidate.get("vector_score", 0))),
                "title": candidate.get("title", ""),
            }
        )

    if hallucinated:
        logger.warning("Dropped %d hallucinated product ids: %s", len(hallucinated), hallucinated)
    if owned:
        logger.info("Dropped %d already-enrolled product ids: %s", len(owned), owned)

    # Top up only if the model under-delivered badly. Padding a good set of three
    # up to four costs a generic "closely matches your interests" line, which
    # reads worse than simply showing three well-argued picks.
    if len(items) < MIN_ITEMS:
        chosen = {i["product_id"] for i in items}
        # Top up from `pool`, not the raw candidate lists: pool has already had
        # owned courses removed. Reading the raw lists here would hand back the
        # exact course we just refused to recommend.
        for candidate in pool.values():
            if len(items) >= MIN_ITEMS:
                break
            if candidate["product_id"] in chosen:
                continue
            chosen.add(candidate["product_id"])
            items.append(
                {
                    "product_id": candidate["product_id"],
                    # The grader's own reasoning is at least specific to this user.
                    "why_this": candidate.get("grade_reason")
                    or "Closely matches the topics you have been exploring.",
                    "relevance_score": float(
                        candidate.get("relevance_score", candidate.get("vector_score", 0))
                    ),
                    "title": candidate.get("title", ""),
                }
            )

    items = items[: settings.reco_max_items]
    for rank, item in enumerate(items):
        item["rank"] = rank

    if not items:
        return {"error": "no groundable recommendations", "node_path": ["finalize"]}

    titles = {c["product_id"]: c.get("title", "") for c in pool.values()}
    return {
        "result": {
            "headline": _clean_copy(
                result.get("headline"), titles
            )[:200] or "Picked for what you have been exploring",
            "narrative": _clean_copy(result.get("narrative"), titles),
            "cta": _clean_copy(result.get("cta"), titles)[:160],
            "items": [
                {**item, "why_this": _clean_copy(item["why_this"], titles)} for item in items
            ],
            "dropped_hallucinations": hallucinated,
        },
        "node_path": ["finalize"],
    }


#: Internal product ids the model was shown, e.g. "[17]" — meaningless to a reader.
_ID_REFERENCE = re.compile(r"\s*\[(\d+)\]")


def _clean_copy(text: str | None, titles: dict[int, str]) -> str:
    """Strip internal database ids out of user-facing copy.

    The prompt tells the model not to write them, but this is the text a person
    actually reads, so it gets a backstop rather than trust. A bracketed id that
    maps to a real candidate becomes that course's title; anything else is
    dropped.
    """
    if not text:
        return ""

    def replace(match: "re.Match[str]") -> str:
        title = titles.get(int(match.group(1)))
        return f" {title}" if title else ""

    return re.sub(r"\s{2,}", " ", _ID_REFERENCE.sub(replace, text)).strip()
