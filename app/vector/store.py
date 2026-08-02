"""Qdrant: the semantic index over the product catalog.

Every product written to Postgres is dual-written here, and the agent's
retrieval node queries *this* store — the recommendations are grounded in real
catalog vectors, not in the model's memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    Range,
    VectorParams,
)

from app.config import settings

logger = logging.getLogger(__name__)

_client: QdrantClient | None = None
_bootstrapped = False


@dataclass(slots=True)
class VectorHit:
    product_id: int
    score: float
    payload: dict[str, Any]


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=15,
        )
    return _client


def set_client(client: QdrantClient | None) -> None:
    """Inject a client (tests use ``QdrantClient(location=':memory:')``)."""
    global _client, _bootstrapped
    _client = client
    _bootstrapped = False


def ensure_collection(force: bool = False) -> None:
    """Create the collection and payload indexes if they don't exist."""
    global _bootstrapped
    if _bootstrapped and not force:
        return

    client = get_client()
    name = settings.qdrant_collection

    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=settings.mesh_embed_dim, distance=Distance.COSINE
            ),
        )
        logger.info("Created Qdrant collection %s", name)

    # Payload indexes power the metadata filtering used by the retrieval node.
    for field, schema in (
        ("category", "keyword"),
        ("level", "keyword"),
        ("is_active", "bool"),
        ("price", "float"),
    ):
        try:
            client.create_payload_index(
                collection_name=name, field_name=field, field_schema=schema
            )
        except (UnexpectedResponse, ValueError):
            pass  # already indexed

    _bootstrapped = True


def upsert_products(points: list[tuple[int, list[float], dict[str, Any]]]) -> None:
    """Write (product_id, vector, payload) triples into Qdrant."""
    if not points:
        return
    ensure_collection()
    get_client().upsert(
        collection_name=settings.qdrant_collection,
        points=[
            PointStruct(id=pid, vector=vector, payload=payload)
            for pid, vector, payload in points
        ],
        wait=True,
    )


def update_payload(product_id: int, payload: dict[str, Any]) -> None:
    """Patch a point's metadata without recomputing its vector.

    This is the cheap path for edits that don't touch the embedded text — a
    price or visibility change costs a payload write, not a Mesh call.
    """
    ensure_collection()
    get_client().set_payload(
        collection_name=settings.qdrant_collection,
        payload=payload,
        points=[product_id],
        wait=True,
    )


def delete_product(product_id: int) -> None:
    ensure_collection()
    get_client().delete(
        collection_name=settings.qdrant_collection,
        points_selector=[product_id],
        wait=True,
    )


def build_filter(
    categories: list[str] | None = None,
    levels: list[str] | None = None,
    max_price: float | None = None,
    exclude_ids: list[int] | None = None,
    active_only: bool = True,
) -> Filter | None:
    """Compose the metadata filter for a retrieval call (retrieval polish)."""
    must: list[FieldCondition] = []
    must_not: list[Any] = []

    if active_only:
        must.append(FieldCondition(key="is_active", match=MatchValue(value=True)))
    if categories:
        must.append(FieldCondition(key="category", match=MatchAny(any=categories)))
    if levels:
        must.append(FieldCondition(key="level", match=MatchAny(any=levels)))
    if max_price is not None:
        must.append(FieldCondition(key="price", range=Range(lte=float(max_price))))
    if exclude_ids:
        must_not.append(
            FieldCondition(key="product_id", match=MatchAny(any=list(exclude_ids)))
        )

    if not must and not must_not:
        return None
    return Filter(must=must or None, must_not=must_not or None)


def search(
    vector: list[float],
    limit: int = 10,
    query_filter: Filter | None = None,
) -> list[VectorHit]:
    """Semantic search over the catalog."""
    ensure_collection()
    response = get_client().query_points(
        collection_name=settings.qdrant_collection,
        query=vector,
        limit=limit,
        query_filter=query_filter,
        with_payload=True,
    )
    return [
        VectorHit(
            product_id=int(point.payload.get("product_id", point.id)),
            score=float(point.score or 0.0),
            payload=dict(point.payload or {}),
        )
        for point in response.points
    ]


def count_points() -> int:
    ensure_collection()
    return int(get_client().count(settings.qdrant_collection, exact=True).count)


def health() -> dict[str, Any]:
    """Used by /healthz and the admin sync panel."""
    try:
        return {"ok": True, "points": count_points()}
    except Exception as exc:  # pragma: no cover - depends on local infra
        return {"ok": False, "error": str(exc)}
