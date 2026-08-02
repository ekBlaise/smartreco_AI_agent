"""Redis: Celery broker, LLM/result cache, and debounce locks.

Redis is a hard dependency of the worker but a *soft* dependency of the web
process — if it is unreachable, event tracking must still work (it falls back to
writing straight to Postgres) rather than 500-ing on the user. Every helper here
degrades instead of raising.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

from app.config import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None
_unavailable_logged = False


def get_redis() -> redis.Redis | None:
    """Return a live client, or None if Redis cannot be reached."""
    global _client, _unavailable_logged
    if _client is not None:
        return _client
    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.5,
            socket_timeout=2.0,
            health_check_interval=30,
        )
        client.ping()
    except Exception as exc:  # pragma: no cover - depends on local infra
        if not _unavailable_logged:
            logger.warning("Redis unavailable (%s); running in degraded mode", exc)
            _unavailable_logged = True
        return None
    _client = client
    return _client


def set_redis(client: redis.Redis | None) -> None:
    """Inject a client (used by tests with fakeredis)."""
    global _client, _unavailable_logged
    _client = client
    _unavailable_logged = False


def cache_get_json(key: str) -> Any | None:
    client = get_redis()
    if client is None:
        return None
    try:
        raw = client.get(key)
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def cache_set_json(key: str, value: Any, ttl_seconds: int) -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        client.setex(key, ttl_seconds, json.dumps(value, default=str))
        return True
    except Exception:
        return False


def cache_delete(*keys: str) -> None:
    client = get_redis()
    if client is None or not keys:
        return
    try:
        client.delete(*keys)
    except Exception:
        pass


def acquire_lock(key: str, ttl_seconds: int) -> bool:
    """Best-effort distributed lock.

    Returns True when the caller owns the lock. When Redis is unavailable we
    return True so that work still happens — a duplicate recommendation is far
    less bad than none at all.
    """
    client = get_redis()
    if client is None:
        return True
    try:
        return bool(client.set(key, "1", nx=True, ex=ttl_seconds))
    except Exception:
        return True


def release_lock(key: str) -> None:
    cache_delete(key)


# --- key builders -----------------------------------------------------------

def reco_lock_key(user_id: int) -> str:
    return f"smartreco:reco:lock:{user_id}"


def reco_signature_key(user_id: int) -> str:
    return f"smartreco:reco:signature:{user_id}"


def reco_cooldown_key(user_id: int) -> str:
    return f"smartreco:reco:cooldown:{user_id}"


def reco_queued_key(user_id: int) -> str:
    """Short-lived marker that a task is already on the queue for this user.

    Distinct from the in-flight lock: this dedupes *enqueueing* (many tracking
    batches arriving at once), while the lock dedupes *executing*.
    """
    return f"smartreco:reco:queued:{user_id}"


def retrieval_cache_key(query_hash: str) -> str:
    return f"smartreco:retrieval:{query_hash}"


def embedding_cache_key(text_hash: str) -> str:
    return f"smartreco:embedding:{text_hash}"
