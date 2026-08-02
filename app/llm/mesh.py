"""Mesh API — the single gateway for every AI call in this project.

The challenge requires that *all* LLM/AI traffic goes through Mesh API. Mesh is
an OpenAI-compatible gateway, so we point the OpenAI SDK (via LangChain's
wrappers, which gives us LangSmith tracing for free) at
``https://api.meshapi.ai/v1``.

This module is the only place in the codebase that constructs a model client.
Chat *and* embeddings both route through Mesh — there is no second provider.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.cache import cache_get_json, cache_set_json, embedding_cache_key
from app.config import settings

logger = logging.getLogger(__name__)

EMBEDDING_CACHE_TTL = 60 * 60 * 24 * 7  # a product's text rarely changes


class MeshNotConfigured(RuntimeError):
    """Raised when MESH_API_KEY is missing — we never silently fake AI output."""


def configure_tracing() -> None:
    """Enable LangSmith tracing (bonus: observability).

    LangChain reads these from the environment, so we mirror our settings into
    it once at process start. Every Mesh call made through the wrappers below
    then shows up as a span inside the agent's trace.
    """
    if not (settings.langsmith_tracing and settings.langsmith_api_key):
        os.environ.setdefault("LANGSMITH_TRACING", "false")
        return
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    logger.info("LangSmith tracing enabled (project=%s)", settings.langsmith_project)


def _require_key() -> str:
    if not settings.mesh_api_key:
        raise MeshNotConfigured(
            "MESH_API_KEY is not set. Every AI call in SmartReco goes through "
            "Mesh API — add your rsk_... key to .env."
        )
    return settings.mesh_api_key


@functools.lru_cache(maxsize=8)
def chat_model(temperature: float = 0.4, model: str | None = None) -> ChatOpenAI:
    """A chat model served by Mesh."""
    return ChatOpenAI(
        model=model or settings.mesh_chat_model,
        base_url=settings.mesh_base_url,
        api_key=_require_key(),
        temperature=temperature,
        timeout=settings.mesh_timeout_seconds,
        max_retries=settings.mesh_max_retries,
    )


@functools.lru_cache(maxsize=1)
def embedding_model() -> OpenAIEmbeddings:
    """The embedding model served by Mesh (used for catalog + query vectors)."""
    return OpenAIEmbeddings(
        model=settings.mesh_embed_model,
        base_url=settings.mesh_base_url,
        api_key=_require_key(),
        # Mesh proxies many providers whose tokenizers tiktoken doesn't know;
        # skipping the local context-length check avoids a needless failure.
        check_embedding_ctx_length=False,
        timeout=settings.mesh_timeout_seconds,
        max_retries=settings.mesh_max_retries,
    )


def _text_hash(text: str) -> str:
    payload = f"{settings.mesh_embed_model}::{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def embed_texts(texts: list[str], use_cache: bool = True) -> list[list[float]]:
    """Embed a batch of texts through Mesh, reusing cached vectors.

    Caching matters here: re-seeding or re-syncing an unchanged catalog should
    cost zero API calls.
    """
    if not texts:
        return []

    results: list[list[float] | None] = [None] * len(texts)
    pending: list[tuple[int, str]] = []

    for idx, text in enumerate(texts):
        if use_cache:
            cached = cache_get_json(embedding_cache_key(_text_hash(text)))
            if isinstance(cached, list) and cached:
                results[idx] = cached
                continue
        pending.append((idx, text))

    if pending:
        vectors = embedding_model().embed_documents([t for _, t in pending])
        for (idx, text), vector in zip(pending, vectors, strict=True):
            results[idx] = vector
            if use_cache:
                cache_set_json(embedding_cache_key(_text_hash(text)), vector, EMBEDDING_CACHE_TTL)

    return [v for v in results if v is not None]


def embed_query(text: str, use_cache: bool = True) -> list[float]:
    """Embed a single retrieval query through Mesh."""
    if use_cache:
        cached = cache_get_json(embedding_cache_key(_text_hash(text)))
        if isinstance(cached, list) and cached:
            return cached
    vector = embedding_model().embed_query(text)
    if use_cache:
        cache_set_json(embedding_cache_key(_text_hash(text)), vector, EMBEDDING_CACHE_TTL)
    return vector
