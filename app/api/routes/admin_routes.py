"""Admin: product CRUD with the dual-write to Qdrant, plus agent observability.

Every mutation writes to Postgres *and* the vector store in the same request.
When the vector write fails the SQL write still commits, the row is flagged, and
``reconcile_vector_store`` picks it up — so the stores converge instead of
silently drifting.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import health
from app.api import admin_metrics
from app.api.deps import require_admin_page
from app.api.schemas import ProductIn
from app.api.templating import templates
from app.database import get_db
from app.models import AgentRun, Product, User
from app.service import recommendation_payload
from app.vector import sync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


def _back(notice: str = "", error: str = "") -> RedirectResponse:
    """Return to the product list carrying a message."""
    key, message = ("error", error) if error else ("notice", notice)
    target = f"/admin/products?{key}={quote(message)}" if message else "/admin/products"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "course"


def unique_slug(db: Session, title: str, exclude_id: int | None = None) -> str:
    base = slugify(title)
    candidate = base
    suffix = 2
    while True:
        stmt = select(Product.id).where(Product.slug == candidate)
        if exclude_id is not None:
            stmt = stmt.where(Product.id != exclude_id)
        if db.scalar(stmt) is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


def _parse_form(
    title: str, description: str, category: str, level: str, price: str,
    instructor: str, duration_hours: str, rating: str, tags: str, is_active: bool,
    curriculum: str = "",
) -> ProductIn:
    def num(raw: str, default: float = 0.0) -> float:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    return ProductIn(
        title=title.strip(),
        description=description.strip(),
        category=category.strip(),
        level=level if level in {"beginner", "intermediate", "advanced"} else "beginner",
        price=num(price),
        instructor=instructor.strip(),
        duration_hours=num(duration_hours),
        rating=min(5.0, max(0.0, num(rating))),
        tags=[t.strip() for t in (tags or "").split(",") if t.strip()],
        curriculum=[m.strip() for m in (curriculum or "").splitlines() if m.strip()],
        is_active=is_active,
    )


def _apply(product: Product, payload: ProductIn) -> None:
    product.title = payload.title
    product.description = payload.description
    product.category = payload.category
    product.level = payload.level
    product.price = payload.price
    product.currency = payload.currency
    product.instructor = payload.instructor
    product.duration_hours = payload.duration_hours
    product.rating = payload.rating
    product.tags = payload.tags
    product.curriculum = payload.curriculum
    product.is_active = payload.is_active


def _dual_write(db: Session, product: Product) -> str | None:
    """Push the product into Qdrant. Returns an error message, or None.

    ``sync_products`` reports failure in its return value rather than raising —
    it flags the row for the reconciler and carries on with the rest of the
    batch. So the result has to be *inspected*: catching exceptions alone would
    report a confident success while the product sat unindexed and invisible to
    every recommendation.
    """
    try:
        result = sync.sync_product(db, product, force=True)
    except Exception as exc:  # safety net — sync is not expected to raise
        logger.exception("Vector write raised for product %s", product.id)
        product.vector_sync_error = f"{type(exc).__name__}: {exc}"[:500]
        db.flush()
        return _explain_vector_failure(product.vector_sync_error)

    if result.get("failed"):
        return _explain_vector_failure(product.vector_sync_error)
    return None


def _explain_vector_failure(raw: str | None) -> str:
    """Turn a driver-level error into something an admin can act on."""
    detail = raw or "unknown error"
    prefix = (
        "Saved to the database, but it is NOT searchable yet — the vector write failed. "
    )
    retry = " It will be retried automatically every 15 minutes."

    if "spend_limit" in detail or "402" in detail:
        return (
            prefix
            + "Mesh refused the embedding call because the account has no balance. "
            "Embedding models are paid; add credit and the product will be indexed."
            + retry
        )
    if "Connection" in detail or "refused" in detail or "timed out" in detail:
        return prefix + "Qdrant is unreachable." + retry
    return prefix + detail[:200] + retry


def _filtered_products(db: Session, q: str) -> list[Product]:
    """Catalog rows for the admin table, optionally filtered by title."""
    stmt = select(Product).order_by(Product.updated_at.desc())
    if q.strip():
        stmt = stmt.where(Product.title.ilike(f"%{q.strip()}%"))
    return list(db.scalars(stmt.limit(200)))


@router.get("", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_page),
):
    """Operational overview: is it healthy, is it working, is it efficient?"""
    components = health.snapshot(db)
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user": admin,
            "components": components,
            "overall": health.worst(components),
            # A full buffer means something different depending on whether
            # anything is draining it, so the page needs to know.
            "worker_online": any(
                c["name"] == "Celery worker" and c["status"] == health.OK for c in components
            ),
            "sync": sync.sync_status(db),
            "catalog": admin_metrics.catalog(db),
            "ingest": admin_metrics.ingest(db),
            "efficiency": admin_metrics.efficiency(db),
            "recos": admin_metrics.recommendations(db),
            "audience": admin_metrics.audience(db),
            "demand": admin_metrics.demand(db),
            "recent_runs": admin_metrics.recent_runs(db),
            "categories": [],
        },
    )


@router.get("/products", response_class=HTMLResponse)
def product_list(
    request: Request,
    q: str = "",
    notice: str = "",
    error: str = "",
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_page),
):
    return templates.TemplateResponse(
        request,
        "admin/products.html",
        {
            "user": admin,
            "products": _filtered_products(db, q),
            "q": q,
            "notice": notice,
            "error": error,
            "sync": sync.sync_status(db),
            "categories": [],
        },
    )


@router.get("/products/table", response_class=HTMLResponse)
def product_table(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_page),
):
    """Just the catalog table, for the live filter.

    Rendered from the same partial as the full page, so the rows — including
    the Alpine bindings on the Edit buttons — are identical either way.
    """
    return templates.TemplateResponse(
        request,
        "admin/_product_table.html",
        {"user": admin, "products": _filtered_products(db, q), "q": q},
    )


@router.post("/products")
def create_product(
    title: str = Form(...),
    description: str = Form(""),
    category: str = Form(...),
    level: str = Form("beginner"),
    price: str = Form("0"),
    instructor: str = Form(""),
    duration_hours: str = Form("0"),
    rating: str = Form("0"),
    tags: str = Form(""),
    curriculum: str = Form(""),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_page),
):
    try:
        payload = _parse_form(
            title, description, category, level, price,
            instructor, duration_hours, rating, tags, is_active, curriculum,
        )
    except ValidationError as exc:
        return _back(error=_first_error(exc))

    product = Product(slug=unique_slug(db, payload.title))
    _apply(product, payload)
    db.add(product)
    db.flush()

    warning = _dual_write(db, product)
    db.commit()

    if warning:
        return _back(error=warning)
    return _back(notice=f"Created “{product.title}” and indexed it for semantic search.")


@router.post("/products/{product_id}")
def update_product(
    product_id: int,
    title: str = Form(...),
    description: str = Form(""),
    category: str = Form(...),
    level: str = Form("beginner"),
    price: str = Form("0"),
    instructor: str = Form(""),
    duration_hours: str = Form("0"),
    rating: str = Form("0"),
    tags: str = Form(""),
    curriculum: str = Form(""),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_page),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    try:
        payload = _parse_form(
            title, description, category, level, price,
            instructor, duration_hours, rating, tags, is_active, curriculum,
        )
    except ValidationError as exc:
        return _back(error=_first_error(exc))

    if payload.title != product.title:
        product.slug = unique_slug(db, payload.title, exclude_id=product.id)
    _apply(product, payload)
    db.flush()

    warning = _dual_write(db, product)
    db.commit()

    if warning:
        return _back(error=warning)
    return _back(notice=f"Updated “{product.title}” in both stores.")


@router.post("/products/{product_id}/delete")
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_page),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    title = product.title
    # Vector first: if this fails we abort rather than orphan a point in Qdrant
    # that would keep surfacing a course the catalog no longer has.
    try:
        sync.remove_product(product.id)
    except Exception:
        return _back(error="Could not remove the vector entry; the product was not deleted.")

    db.delete(product)
    db.commit()
    return _back(notice=f"Deleted “{title}” from both stores.")


@router.post("/resync")
def resync(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_page),
):
    """Force-push every out-of-sync product into Qdrant now."""
    stale = sync.find_out_of_sync(db, limit=500)
    if not stale:
        return _back(notice="Both stores are already in sync.")
    try:
        result = sync.sync_products(db, stale, force=True)
        db.commit()
    except Exception as exc:
        return _back(error=f"Resync failed: {type(exc).__name__}: {exc}")
    return _back(notice=f"Re-indexed {result['embedded']} product(s).")


@router.get("/agent-runs", response_class=HTMLResponse)
def agent_runs(
    request: Request,
    status_filter: str = Query("", alias="status"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin_page),
):
    """Observability: what the agent did, how often, and how much it cost."""
    stmt = select(AgentRun).order_by(AgentRun.created_at.desc())
    if status_filter:
        stmt = stmt.where(AgentRun.status == status_filter)
    runs = list(db.scalars(stmt.limit(60)))

    # The recommendation each run produced, so a row can be expanded to show
    # what the node path actually resulted in — the point of the whole run.
    produced = {
        run.recommendation_id: recommendation_payload(run.recommendation)
        for run in runs
        if run.recommendation_id and run.recommendation is not None
    }

    return templates.TemplateResponse(
        request,
        "admin/agent_runs.html",
        {
            "user": admin,
            "runs": runs,
            "produced": produced,
            "status_filter": status_filter,
            "efficiency": admin_metrics.efficiency(db),
            "ingest": admin_metrics.ingest(db),
            "categories": [],
        },
    )


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid input."
    err = errors[0]
    field = ".".join(str(p) for p in err.get("loc", ())) or "field"
    return f"{field}: {err.get('msg', 'invalid')}"
