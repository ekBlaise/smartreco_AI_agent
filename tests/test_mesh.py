"""Mesh API is the only AI gateway.

The challenge requires that *every* LLM and embedding call goes through Mesh.
These tests pin that: the clients are pointed at the Mesh base URL, and no other
provider SDK is constructed anywhere in the source tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import BASE_DIR, settings
from app.llm import mesh


def test_chat_client_is_pointed_at_mesh():
    model = mesh.chat_model.__wrapped__(temperature=0.3)  # bypass the lru_cache

    assert str(model.openai_api_base).rstrip("/") == settings.mesh_base_url.rstrip("/")
    assert "meshapi.ai" in str(model.openai_api_base)
    assert model.model_name == settings.mesh_chat_model


def test_embedding_client_is_pointed_at_mesh():
    embeddings = mesh.embedding_model.__wrapped__()

    assert "meshapi.ai" in str(embeddings.openai_api_base)
    assert embeddings.model == settings.mesh_embed_model


def test_missing_key_fails_loudly_rather_than_faking_output(monkeypatch):
    monkeypatch.setattr(settings, "mesh_api_key", "")
    mesh.chat_model.cache_clear()
    mesh.embedding_model.cache_clear()

    with pytest.raises(mesh.MeshNotConfigured):
        mesh.chat_model(temperature=0.2)
    with pytest.raises(mesh.MeshNotConfigured):
        mesh.embedding_model()

    mesh.chat_model.cache_clear()
    mesh.embedding_model.cache_clear()


def test_no_other_llm_provider_is_used_anywhere():
    """A grep-level guard against a second provider sneaking in."""
    forbidden = re.compile(
        r"\b(anthropic|google\.generativeai|from\s+groq|import\s+groq|mistralai|cohere|ollama"
        r"|api\.openai\.com|generativeai)\b",
        re.I,
    )
    offenders = []
    for path in (BASE_DIR / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if forbidden.search(text):
            offenders.append(str(path.relative_to(BASE_DIR)))

    assert offenders == [], f"non-Mesh LLM provider referenced in: {offenders}"


def test_only_the_mesh_module_constructs_model_clients():
    """Every AI call funnels through app/llm/mesh.py."""
    offenders = []
    for path in (BASE_DIR / "app").rglob("*.py"):
        if path.name == "mesh.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\b(ChatOpenAI|OpenAIEmbeddings)\s*\(", text):
            offenders.append(str(path.relative_to(BASE_DIR)))

    assert offenders == [], f"model client built outside app/llm/mesh.py: {offenders}"


def test_no_api_key_is_committed():
    """Secrets live in a gitignored .env, never in the tree."""
    key_pattern = re.compile(r"rsk_[A-Za-z0-9]{12,}")
    offenders = []
    for path in Path(BASE_DIR).rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.parts)
        if parts & {".git", ".venv", "data", "__pycache__", ".pytest_cache", "node_modules"}:
            continue
        if path.name == ".env":
            continue  # gitignored, and the developer's own file
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if key_pattern.search(text):
            offenders.append(str(path.relative_to(BASE_DIR)))

    assert offenders == [], f"possible committed Mesh key in: {offenders}"


def test_gitignore_excludes_env():
    gitignore = (BASE_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in gitignore]
