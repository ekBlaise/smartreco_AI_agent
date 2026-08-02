"""Seed the catalog into Postgres AND Qdrant (the dual-write, from a script).

    python seed.py            # create tables, upsert catalog + demo accounts
    python seed.py --reset    # drop everything first
    python seed.py --no-vectors   # SQL only (skips Mesh embedding calls)
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import select

from app.catalog_data import COURSES
from app.config import settings
from app.database import Base, engine, init_db, session_scope
from app.models import Product, Role, User
from app.security import hash_password
from app.vector import store, sync

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("seed")

DEMO_USERS = [
    {
        "email": "admin@smartreco.dev",
        "full_name": "Ada Admin",
        "password": "admin1234",
        "role": Role.ADMIN.value,
    },
    {
        "email": "user@smartreco.dev",
        "full_name": "Riley Learner",
        "password": "user1234",
        "role": Role.USER.value,
    },
]


def seed_users(session) -> int:
    created = 0
    for spec in DEMO_USERS:
        existing = session.scalar(select(User).where(User.email == spec["email"]))
        if existing:
            continue
        session.add(
            User(
                email=spec["email"],
                full_name=spec["full_name"],
                password_hash=hash_password(spec["password"]),
                role=spec["role"],
            )
        )
        created += 1
    session.flush()
    return created


def seed_products(session) -> tuple[int, int, list[Product]]:
    created = updated = 0
    products: list[Product] = []
    for spec in COURSES:
        product = session.scalar(select(Product).where(Product.slug == spec["slug"]))
        if product is None:
            product = Product(slug=spec["slug"])
            session.add(product)
            created += 1
        else:
            updated += 1
        product.title = spec["title"]
        product.description = spec["description"]
        product.category = spec["category"]
        product.level = spec["level"]
        product.price = spec["price"]
        product.instructor = spec["instructor"]
        product.duration_hours = spec["duration_hours"]
        product.rating = spec["rating"]
        product.enrollments = spec["enrollments"]
        product.tags = spec["tags"]
        product.is_active = True
        products.append(product)
    session.flush()
    return created, updated, products


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the SmartReco catalog.")
    parser.add_argument("--reset", action="store_true", help="drop all tables first")
    parser.add_argument(
        "--no-vectors",
        action="store_true",
        help="skip the Qdrant dual-write (no Mesh embedding calls)",
    )
    args = parser.parse_args()

    if args.reset:
        logger.warning("Dropping all tables in %s", engine.url)
        Base.metadata.drop_all(bind=engine)

    init_db()

    with session_scope() as session:
        users = seed_users(session)
        created, updated, products = seed_products(session)
        logger.info(
            "SQL: %d products created, %d updated, %d demo users created",
            created,
            updated,
            users,
        )

        if args.no_vectors:
            logger.warning("Skipping vector sync (--no-vectors)")
            return 0

        if not settings.mesh_configured:
            logger.error(
                "MESH_API_KEY is not set — cannot embed the catalog. "
                "Add it to .env, or re-run with --no-vectors for SQL only."
            )
            return 1

        if args.reset:
            try:
                store.get_client().delete_collection(settings.qdrant_collection)
                store.ensure_collection(force=True)
                logger.info("Recreated Qdrant collection %s", settings.qdrant_collection)
            except Exception as exc:
                logger.warning("Could not reset the Qdrant collection: %s", exc)

        result = sync.sync_products(session, products, force=args.reset)
        logger.info(
            "Vector: %d embedded, %d payload-only, %d already in sync, %d failed",
            result["embedded"],
            result["payload_only"],
            result["skipped"],
            result["failed"],
        )
        first_error = next(
            (p.vector_sync_error for p in products if p.vector_sync_error), None
        )

    with session_scope() as session:
        status = sync.sync_status(session)
    logger.info("Sync status: %s", status)

    if not status["in_sync"]:
        # Be specific about which half failed — "check Qdrant" is unhelpful when
        # the real problem was the embedding call.
        if first_error:
            logger.error("Vector sync failed: %s", first_error)
            if "402" in first_error or "spend_limit" in first_error:
                logger.error(
                    "Mesh rejected the embedding call for billing reasons. Embedding "
                    "models are paid; add credit to your Mesh account, or re-run with "
                    "--no-vectors to seed SQL only."
                )
        elif not status["vector_ok"]:
            logger.error("Qdrant is not reachable at %s", settings.qdrant_url)
        else:
            logger.error("%d product(s) are still not in sync.", status["pending_sync"])
        return 1

    logger.info("Done. Log in as user@smartreco.dev / user1234 (or admin@smartreco.dev / admin1234).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
