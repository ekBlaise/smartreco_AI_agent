"""Products must exist in Postgres AND Qdrant, and stay that way."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Product
from app.vector import store, sync


def test_seeded_catalog_lands_in_both_stores(db, catalog):
    assert db.scalar(select(Product.id).limit(1)) is not None
    assert store.count_points() == len(catalog)
    assert all(p.vector_in_sync for p in catalog)


def test_products_are_semantically_retrievable(db, catalog):
    """The vector store is actually queried, and returns topically right answers."""
    from conftest import fake_embedding

    hits = store.search(fake_embedding("langgraph agents tool calling orchestration"), limit=3)

    assert hits, "semantic search returned nothing"
    titles = [h.payload["title"] for h in hits]
    assert any("Agent" in t for t in titles), titles


def test_update_reembeds_and_resyncs(db, catalog):
    product = catalog[0]
    original_hash = product.embedding_hash

    product.description = "Completely rewritten: kafka partitions consumers streaming pipelines."
    db.flush()
    result = sync.sync_product(db, product, force=False)
    db.commit()

    assert result["embedded"] == 1
    assert product.embedding_hash != original_hash
    assert product.vector_in_sync

    # And the new text is what Qdrant now holds.
    point = store.get_client().retrieve(
        collection_name=store.settings.qdrant_collection, ids=[product.id], with_payload=True
    )[0]
    assert "kafka" in point.payload["summary"].lower()


def test_price_edit_patches_payload_without_re_embedding(db, catalog, monkeypatch):
    """A price change must update Qdrant but must not cost a Mesh embedding call."""
    calls = {"n": 0}
    real = sync.embed_texts

    def counting(texts, use_cache=True):
        calls["n"] += 1
        return real(texts, use_cache)

    monkeypatch.setattr("app.vector.sync.embed_texts", counting)

    product = catalog[0]
    product.price = 149.0  # not part of the embedded document
    db.flush()
    result = sync.sync_products(db, [product])
    db.commit()

    assert calls["n"] == 0, "a price edit must not re-embed"
    assert result["payload_only"] == 1
    assert result["embedded"] == 0
    assert product.vector_in_sync

    # The new price really is in Qdrant.
    point = store.get_client().retrieve(
        collection_name=store.settings.qdrant_collection, ids=[product.id], with_payload=True
    )[0]
    assert point.payload["price"] == 149.0


def test_nothing_changed_costs_nothing(db, catalog, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("must not embed when nothing changed")

    monkeypatch.setattr("app.vector.sync.embed_texts", boom)

    result = sync.sync_products(db, list(catalog))
    assert result == {"embedded": 0, "payload_only": 0, "skipped": len(catalog), "failed": 0}


def test_delete_removes_the_vector(db, catalog):
    product = catalog[0]
    before = store.count_points()

    sync.remove_product(product.id)
    db.delete(product)
    db.commit()

    assert store.count_points() == before - 1
    assert db.get(Product, product.id) is None


def test_out_of_sync_products_are_detectable_and_repairable(db, catalog):
    """The reconcile path: a stale row is found and fixed."""
    product = catalog[0]
    product.embedding_hash = None
    product.payload_hash = None
    db.flush()

    stale = sync.find_out_of_sync(db)
    assert product.id in [p.id for p in stale]

    sync.sync_products(db, stale, force=True)
    db.commit()

    assert sync.find_out_of_sync(db) == []
    assert sync.sync_status(db)["in_sync"] is True


def test_failed_vector_write_is_recorded_not_swallowed(db, catalog, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("qdrant is down")

    monkeypatch.setattr("app.vector.store.upsert_products", boom)

    product = catalog[0]
    product.description = "changed so it needs a re-embed"
    db.flush()
    result = sync.sync_products(db, [product], force=True)

    assert result["failed"] == 1
    assert "qdrant is down" in product.vector_sync_error
    # ...and it is queued for the reconcile task rather than lost.
    assert product.id in [p.id for p in sync.find_out_of_sync(db)]
