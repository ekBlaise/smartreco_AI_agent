"""Test environment.

The suite runs with **no network and no external services**:
  * SQLite instead of Postgres
  * fakeredis instead of Redis
  * an in-memory Qdrant instead of a Qdrant server
  * a deterministic fake in place of every Mesh API call

Real Mesh calls are faked, not stubbed out of existence — the fake still goes
through the same ``chat_model(...).with_structured_output(...).invoke(...)``
surface the production code uses, so the wiring is genuinely exercised.
"""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from pathlib import Path

# Must be set before app.config is imported anywhere.
_TEST_DB = Path(tempfile.gettempdir()) / "smartreco_test.db"
_TEST_DB.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["MESH_API_KEY"] = "rsk_test_key_not_real"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["SMTP_HOST"] = ""
os.environ["REDIS_URL"] = "redis://localhost:6379/15"

import fakeredis  # noqa: E402
import fakeredis.aioredis  # noqa: E402
import pytest  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402

from app import cache, realtime  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Product, Role, User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.vector import store  # noqa: E402


# --- deterministic fake embeddings -----------------------------------------

def fake_embedding(text: str) -> list[float]:
    """A hashed bag-of-words vector.

    Deterministic and, crucially, *similar for similar text* — two courses that
    share vocabulary land near each other, so retrieval tests exercise real
    ranking rather than returning an arbitrary order.
    """
    dim = settings.mesh_embed_dim
    vector = [0.0] * dim
    for token in (text or "").lower().split():
        digest = hashlib.md5(token.encode()).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        vector[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


@pytest.fixture(autouse=True)
def fake_mesh(monkeypatch):
    """Replace every Mesh call site with the deterministic fake."""
    from tests.fakes import FakeChatModel

    def _embed_texts(texts, use_cache=True):
        return [fake_embedding(t) for t in texts]

    def _embed_query(text, use_cache=True):
        return fake_embedding(text)

    monkeypatch.setattr("app.vector.sync.embed_texts", _embed_texts)
    monkeypatch.setattr("app.agent.nodes.embed_query", _embed_query)
    monkeypatch.setattr("app.llm.mesh.embed_texts", _embed_texts)
    monkeypatch.setattr("app.llm.mesh.embed_query", _embed_query)

    chat = FakeChatModel()
    monkeypatch.setattr("app.agent.nodes.chat_model", lambda **kw: chat)
    return chat


# --- infrastructure fakes ---------------------------------------------------

@pytest.fixture(autouse=True)
def fake_redis():
    """One fake Redis, shared by the sync client and the async pub/sub client.

    Both must see the same keyspace: a test publishes through the sync client
    and reads it back through the SSE stream's async one. Without the async
    factory override, `realtime.subscribe` would connect to whatever Redis is
    actually listening on this machine — so the suite would pass, fail, or hang
    depending on whether a container happened to be running.
    """
    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    cache.set_redis(client)
    realtime.set_async_client_factory(
        lambda: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    )
    yield client
    client.flushall()
    cache.set_redis(None)
    realtime.set_async_client_factory(None)


@pytest.fixture(autouse=True)
def vector_store():
    """A fresh in-memory Qdrant per test."""
    client = QdrantClient(location=":memory:")
    store.set_client(client)
    store.ensure_collection(force=True)
    yield client
    store.set_client(None)


@pytest.fixture(autouse=True)
def db():
    """A clean schema per test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# --- data builders ----------------------------------------------------------

@pytest.fixture
def admin(db) -> User:
    user = User(
        email="admin@test.dev",
        full_name="Test Admin",
        password_hash=hash_password("admin1234"),
        role=Role.ADMIN.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def learner(db) -> User:
    user = User(
        email="learner@test.dev",
        full_name="Test Learner",
        password_hash=hash_password("learner1234"),
        role=Role.USER.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def catalog(db) -> list[Product]:
    """A small catalog with two clearly separated topical clusters."""
    from app.vector import sync

    specs = [
        ("agentic-langgraph", "Building AI Agents with LangGraph", "Agentic AI", "intermediate",
         "langgraph agents tool calling state machines orchestration reasoning graph"),
        ("agentic-multi", "Multi-Agent Systems in Production", "Agentic AI", "advanced",
         "multi agent orchestration supervisor handoff agents evaluation reasoning"),
        ("agentic-memory", "Agent Memory Architectures", "Agentic AI", "advanced",
         "agents memory episodic semantic vector recall reasoning"),
        ("rag-scratch", "RAG Systems from Scratch", "LLM Engineering", "intermediate",
         "rag retrieval chunking embeddings vector database grounding"),
        ("kafka-streams", "Streaming Data with Kafka", "Data Engineering", "advanced",
         "kafka streaming partitions consumers exactly once pipelines"),
        ("dbt-basics", "Analytics Engineering with dbt", "Data Engineering", "beginner",
         "dbt sql warehouse modeling staging tests analytics"),
    ]
    products = []
    for i, (slug, title, category, level, keywords) in enumerate(specs):
        product = Product(
            slug=slug,
            title=title,
            description=f"{title}. Covers {keywords}.",
            category=category,
            level=level,
            price=99.0 + i * 10,
            instructor="Test Instructor",
            duration_hours=10,
            rating=4.5,
            enrollments=1000 - i * 50,
            tags=keywords.split()[:4],
            is_active=True,
        )
        db.add(product)
        products.append(product)
    db.flush()
    sync.sync_products(db, products)
    db.commit()
    for product in products:
        db.refresh(product)
    return products


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def logged_in(client, learner):
    response = client.post(
        "/login",
        data={"email": learner.email, "password": "learner1234", "next": "/dashboard"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return client


@pytest.fixture
def admin_client(client, admin):
    response = client.post(
        "/login",
        data={"email": admin.email, "password": "admin1234", "next": "/admin"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return client
