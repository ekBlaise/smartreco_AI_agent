"""Dual-write: keep Postgres and Qdrant in sync for every product change.

Sync state is decided by content, not by clocks — see :class:`app.models.Product`.
That buys a useful distinction:

* the **description** changed  -> re-embed through Mesh, then upsert
* only the **price/level/…** changed -> patch the Qdrant payload, no embedding
* nothing changed -> do nothing at all

which is what stops routine admin edits from burning Mesh credits.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.llm.mesh import embed_texts
from app.models import Product
from app.vector import store

logger = logging.getLogger(__name__)


def embedding_text(product: Product) -> str:
    return product.embedding_document


def payload_for(product: Product) -> dict[str, Any]:
    return product.vector_payload


def _mark_synced(product: Product) -> None:
    product.embedding_hash = product.content_digest
    product.payload_hash = product.payload_digest
    product.vector_synced_at = datetime.now(timezone.utc)
    product.vector_sync_error = None


def sync_products(
    session: Session, products: list[Product], force: bool = False
) -> dict[str, int]:
    """Push a batch of products into Qdrant.

    Returns counts of what actually happened, so callers (and tests) can assert
    that unchanged products cost nothing.
    """
    result = {"embedded": 0, "payload_only": 0, "skipped": 0, "failed": 0}
    if not products:
        return result

    to_embed: list[Product] = []
    to_patch: list[Product] = []

    for product in products:
        if force or product.needs_embedding:
            to_embed.append(product)
        elif product.payload_hash != product.payload_digest:
            to_patch.append(product)
        else:
            result["skipped"] += 1

    if to_embed:
        try:
            vectors = embed_texts([p.embedding_document for p in to_embed])
            store.upsert_products(
                [
                    (p.id, vector, p.vector_payload)
                    for p, vector in zip(to_embed, vectors, strict=True)
                ]
            )
        except Exception as exc:
            _record_failure(session, to_embed, exc)
            result["failed"] += len(to_embed)
            to_embed = []
        else:
            for product in to_embed:
                _mark_synced(product)
            result["embedded"] = len(to_embed)

    for product in to_patch:
        try:
            store.update_payload(product.id, product.vector_payload)
        except Exception as exc:
            _record_failure(session, [product], exc)
            result["failed"] += 1
        else:
            _mark_synced(product)
            result["payload_only"] += 1

    session.flush()
    if result["embedded"] or result["payload_only"]:
        logger.info(
            "Vector sync: %d embedded, %d payload-only, %d skipped, %d failed",
            result["embedded"], result["payload_only"], result["skipped"], result["failed"],
        )
    return result


def _record_failure(session: Session, products: list[Product], exc: Exception) -> None:
    """Flag the rows so ``reconcile_vector_store`` retries them."""
    logger.exception("Vector sync failed for %d product(s)", len(products))
    message = f"{type(exc).__name__}: {exc}"[:500]
    for product in products:
        product.vector_sync_error = message
    session.flush()


def sync_product(session: Session, product: Product, force: bool = False) -> dict[str, int]:
    return sync_products(session, [product], force=force)


def remove_product(product_id: int) -> None:
    """Delete side of the dual-write."""
    try:
        store.delete_product(product_id)
    except Exception:
        logger.exception("Failed to delete product %s from Qdrant", product_id)
        raise


def find_out_of_sync(session: Session, limit: int = 500) -> list[Product]:
    """Products whose Qdrant copy is missing, stale or known-broken.

    The hash comparison happens in Python because it is a content check, not a
    column comparison. A course catalog is small enough that scanning it is
    cheaper than maintaining a denormalised flag that could itself go stale.
    """
    products = session.scalars(select(Product).order_by(Product.id).limit(limit)).all()
    return [p for p in products if not p.vector_in_sync]


def sync_status(session: Session) -> dict[str, Any]:
    """Summary shown on the admin page."""
    total = int(session.scalar(select(func.count(Product.id))) or 0)
    pending = len(find_out_of_sync(session))
    vector = store.health()
    return {
        "products": total,
        "pending_sync": pending,
        "vector_points": vector.get("points"),
        "vector_ok": vector.get("ok", False),
        "vector_error": vector.get("error"),
        "in_sync": vector.get("ok", False) and pending == 0,
    }
