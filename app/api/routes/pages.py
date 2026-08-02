"""Server-rendered pages: home, catalog, search, product, dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_user_optional, get_session_id, login_redirect
from app.api.templating import templates
from app.database import get_db
from app.ingest import triggers
from app.models import Product, User
from app.service import recommendation_payload, recommendation_status

router = APIRouter(tags=["pages"])

PAGE_SIZE = 12


def _categories(db: Session) -> list[str]:
    return list(
        db.scalars(
            select(distinct(Product.category))
            .where(Product.is_active.is_(True))
            .order_by(Product.category)
        )
    )


def render(request: Request, template: str, user: User | None, context: dict, **kwargs):
    """Render a page with the context every template expects."""
    return templates.TemplateResponse(
        request,
        template,
        {"user": user, "session_id": get_session_id(request), **context},
        **kwargs,
    )


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
):
    featured = list(
        db.scalars(
            select(Product)
            .where(Product.is_active.is_(True))
            .order_by(Product.enrollments.desc())
            .limit(6)
        )
    )
    recommendation = None
    if user is not None:
        current = triggers.current_recommendation(db, user.id)
        if current is not None:
            recommendation = recommendation_payload(current)

    return render(
        request,
        "index.html",
        user,
        {
            "featured": featured,
            "categories": _categories(db),
            "recommendation": recommendation,
            "total_courses": db.scalar(select(func.count(Product.id))) or 0,
        },
    )


@router.get("/catalog", response_class=HTMLResponse)
def catalog(
    request: Request,
    category: str = "",
    level: str = "",
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
):
    stmt = select(Product).where(Product.is_active.is_(True))
    count_stmt = select(func.count(Product.id)).where(Product.is_active.is_(True))
    if category:
        stmt = stmt.where(Product.category == category)
        count_stmt = count_stmt.where(Product.category == category)
    if level:
        stmt = stmt.where(Product.level == level)
        count_stmt = count_stmt.where(Product.level == level)

    total = db.scalar(count_stmt) or 0
    products = list(
        db.scalars(
            stmt.order_by(Product.enrollments.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    )

    return render(
        request,
        "catalog.html",
        user,
        {
            "products": products,
            "categories": _categories(db),
            "category": category,
            "level": level,
            "page": page,
            "total": total,
            "has_next": page * PAGE_SIZE < total,
        },
    )


@router.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
):
    """Keyword search over the catalog.

    Deliberately plain SQL matching. Semantic retrieval is the *agent's* job —
    it runs over Qdrant with behaviour-derived queries, not over whatever the
    user last typed into this box.
    """
    products: list[Product] = []
    if q.strip():
        pattern = f"%{q.strip()}%"
        products = list(
            db.scalars(
                select(Product)
                .where(
                    Product.is_active.is_(True),
                    or_(
                        Product.title.ilike(pattern),
                        Product.description.ilike(pattern),
                        Product.category.ilike(pattern),
                        Product.instructor.ilike(pattern),
                    ),
                )
                .order_by(Product.enrollments.desc())
                .limit(24)
            )
        )

    return render(
        request,
        "search.html",
        user,
        {"q": q, "products": products, "categories": _categories(db)},
    )


@router.get("/course/{slug}", response_class=HTMLResponse)
def product_detail(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
):
    product = db.scalar(select(Product).where(Product.slug == slug))
    if product is None:
        return render(
            request, "not_found.html", user, {"categories": _categories(db)}, status_code=404
        )

    related = list(
        db.scalars(
            select(Product)
            .where(
                Product.category == product.category,
                Product.id != product.id,
                Product.is_active.is_(True),
            )
            .order_by(Product.rating.desc())
            .limit(3)
        )
    )

    return render(
        request,
        "product.html",
        user,
        {"product": product, "related": related, "categories": _categories(db)},
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
):
    if user is None:
        return login_redirect("/dashboard")

    history = [
        {"version": r.version, "headline": r.headline, "created_at": r.created_at}
        for r in user.recommendations[:6]
    ]

    return render(
        request,
        "dashboard.html",
        user,
        {
            "status": recommendation_status(db, user),
            "history": history,
            "categories": _categories(db),
        },
    )
