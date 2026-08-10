"""Dependency health, for the admin dashboard.

Four of this system's five moving parts fail *silently and partially*: if Qdrant
is down recommendations get thinner rather than erroring, if Redis is down
tracking still works but nothing is deduped, if the Celery broker is unreachable
generation falls back to inline, and if Mesh is unconfigured the agent records an
``unconfigured`` run instead of raising. All useful behaviour — and all of it
means an operator can be badly degraded without a single error reaching them.

So the dashboard asks each dependency directly. Every probe is cheap, bounded,
and returns a verdict rather than raising.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.cache import get_redis
from app.config import settings
from app.vector import store

logger = logging.getLogger(__name__)

OK = "ok"
DEGRADED = "degraded"
DOWN = "down"


def _probe(name: str, essential: bool = True) -> dict[str, Any]:
    return {"name": name, "status": DOWN, "detail": "", "essential": essential}


def database(session: Session) -> dict[str, Any]:
    result = _probe("PostgreSQL")
    try:
        session.execute(text("SELECT 1"))
        result["status"] = OK
        # Report the engine actually in use — the SQLite fallback is easy to be
        # on by accident, and it is not what you want in front of a demo.
        url = settings.sqlalchemy_url
        result["detail"] = "SQLite fallback" if url.startswith("sqlite") else "connected"
        if url.startswith("sqlite"):
            result["status"] = DEGRADED
    except Exception as exc:
        result["detail"] = type(exc).__name__
    return result


def vector_store() -> dict[str, Any]:
    result = _probe("Qdrant")
    health = store.health()
    if health.get("ok"):
        result["status"] = OK
        result["detail"] = f"{health.get('points', 0)} vectors"
    else:
        result["detail"] = (health.get("error") or "unreachable")[:80]
    return result


def redis() -> dict[str, Any]:
    result = _probe("Redis", essential=False)
    client = get_redis()
    if client is None:
        result["status"] = DEGRADED
        result["detail"] = "unreachable — tracking still works, nothing is cached"
        return result
    try:
        info = client.info("server")
        result["status"] = OK
        result["detail"] = f"v{info.get('redis_version', '?')}"
    except Exception as exc:
        result["status"] = DEGRADED
        result["detail"] = type(exc).__name__
    return result


def ping_workers(timeout: float = 0.7) -> list[dict]:
    """Ask the Celery workers to identify themselves. Never raises.

    The distinction that matters: the *broker* being up says nothing about
    whether anything is consuming. With Redis as the broker, `apply_async`
    happily succeeds into a queue no worker is reading, and every recommendation
    silently never happens.
    """
    try:
        from app.workers.celery_app import celery_app

        return celery_app.control.ping(timeout=timeout) or []
    except Exception:
        return []


def broker() -> dict[str, Any]:
    """Is a Celery worker actually consuming? Not just: is the broker up?"""
    result = _probe("Celery worker", essential=False)
    try:
        replies = ping_workers()
    except Exception as exc:  # pragma: no cover - ping_workers swallows already
        result["status"] = DEGRADED
        result["detail"] = f"{type(exc).__name__} — generation falls back to inline"
        return result

    if replies:
        names = [name for reply in replies for name in reply]
        result["status"] = OK
        result["detail"] = f"{len(names)} online"
    else:
        result["status"] = DEGRADED
        result["detail"] = "no worker responding — generation falls back to inline"
    return result


def mesh() -> dict[str, Any]:
    """Configuration only — deliberately no live call.

    Rendering an admin page must never spend money or block on a third party.
    """
    result = _probe("Mesh API")
    if settings.mesh_configured:
        result["status"] = OK
        result["detail"] = settings.mesh_chat_model
    else:
        result["detail"] = "MESH_API_KEY not set — the agent cannot run"
    return result


def tracing() -> dict[str, Any]:
    result = _probe("LangSmith", essential=False)
    if settings.langsmith_tracing and settings.langsmith_api_key:
        result["status"] = OK
        result["detail"] = settings.langsmith_project
    else:
        result["status"] = DEGRADED
        result["detail"] = "tracing off"
    return result


def snapshot(session: Session) -> list[dict[str, Any]]:
    """Every dependency, in the order an operator would triage them."""
    return [
        database(session),
        vector_store(),
        mesh(),
        redis(),
        broker(),
        tracing(),
    ]


def worst(components: list[dict[str, Any]]) -> str:
    """Overall verdict: an essential dependency down outranks anything else."""
    if any(c["status"] == DOWN and c["essential"] for c in components):
        return DOWN
    if any(c["status"] != OK for c in components):
        return DEGRADED
    return OK
