"""The recommendation agent as an explicit LangGraph workflow.

    profile_behavior -> plan_queries -> retrieve -> grade_candidates
                                          ^              |
                                          |              v
                                     refine_queries <- (enough good matches?)
                                                         |
                                                         v
                                                     generate -> finalize

The conditional edge after grading is the interesting part: the agent evaluates
its own retrieval quality and loops back to search differently when the results
do not actually serve the user, rather than generating persuasive copy about
courses that do not fit.
"""

from __future__ import annotations

import functools
import logging

from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.state import RecoState
from app.config import settings

logger = logging.getLogger(__name__)

MIN_GOOD_CANDIDATES = 3


def route_after_grading(state: RecoState) -> str:
    """Decide: generate now, or retrieve again with better queries?"""
    graded = state.get("graded") or []
    attempts = state.get("attempts") or 0

    if len(graded) >= MIN_GOOD_CANDIDATES:
        return "generate"
    if attempts >= settings.reco_max_retrieval_attempts:
        # Out of budget — generate with the best of what we have rather than
        # returning nothing.
        logger.info("Retrieval budget exhausted after %d attempts; generating anyway", attempts)
        return "generate"
    return "refine_queries"


def build_graph() -> StateGraph:
    graph = StateGraph(RecoState)

    graph.add_node("profile_behavior", nodes.profile_behavior)
    graph.add_node("plan_queries", nodes.plan_queries)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("grade_candidates", nodes.grade_candidates)
    graph.add_node("refine_queries", nodes.refine_queries)
    graph.add_node("generate", nodes.generate)
    graph.add_node("finalize", nodes.finalize)

    graph.add_edge(START, "profile_behavior")
    graph.add_edge("profile_behavior", "plan_queries")
    graph.add_edge("plan_queries", "retrieve")
    graph.add_edge("retrieve", "grade_candidates")
    graph.add_conditional_edges(
        "grade_candidates",
        route_after_grading,
        {"generate": "generate", "refine_queries": "refine_queries"},
    )
    graph.add_edge("refine_queries", "retrieve")
    graph.add_edge("generate", "finalize")
    graph.add_edge("finalize", END)

    return graph


@functools.lru_cache(maxsize=1)
def get_agent():
    """Compiled graph, built once per process."""
    return build_graph().compile()


def run_agent(state: RecoState) -> RecoState:
    """Invoke the agent. Traced end to end by LangSmith when enabled."""
    agent = get_agent()
    return agent.invoke(
        state,
        config={
            "run_name": "smartreco_recommendation",
            "metadata": {
                "user_id": state.get("user_id"),
                "trigger": state.get("trigger", "behavior"),
            },
            "tags": ["smartreco", "recommendation"],
            "recursion_limit": 25,
        },
    )
