"""Server-rendered pages: home, catalog, search, product, dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_user_optional, get_session_id, login_redirect
from app.api.templating import templates
from app.config import settings
from app.api.routes.cart import cart_product_ids
from app.audience import Audience
from app.catalog_people import initials, instructor_profile
from app.database import get_db
from app.ingest import triggers
from app.models import Enrollment, Event, Product, Recommendation, User
from app.service import owns_enrollment, recommendation_payload, recommendation_status

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


def render(
    request: Request,
    template: str,
    user: User | None,
    context: dict,
    db: Session | None = None,
    **kwargs,
):
    """Render a page with the context every template expects.

    ``db`` is optional only so the few pages with nothing to look up stay
    simple; pass it wherever the header cart badge should be accurate.
    """
    session_id = get_session_id(request)
    count = 0
    if db is not None and session_id:
        count = len(cart_product_ids(db, session_id))

    return templates.TemplateResponse(
        request,
        template,
        {
            "user": user,
            "session_id": session_id,
            # On every page, so it is resolved here rather than in each route.
            "cart_count": count,
            **context,
        },
        **kwargs,
    )


VIEW_EVENTS = ("product_view", "product_click", "enroll_intent", "reco_click")


def also_explored(db: Session, product: Product, limit: int = 3) -> list[Product]:
    """Courses opened by the same people who opened this one.

    Real co-viewership from the events table — the behavioural signal the whole
    platform is built on — rather than "more from this category", which is just
    the catalog talking to itself. Falls back to category when a course is too
    new to have been co-viewed with anything.
    """
    viewers = (
        select(Event.session_id)
        .where(
            Event.product_id == product.id,
            Event.type.in_(VIEW_EVENTS),
            Event.session_id.is_not(None),
        )
        .distinct()
        .scalar_subquery()
    )
    ranked = (
        select(Event.product_id, func.count(func.distinct(Event.session_id)).label("shared"))
        .where(
            Event.session_id.in_(viewers),
            Event.product_id.is_not(None),
            Event.product_id != product.id,
            Event.type.in_(VIEW_EVENTS),
        )
        .group_by(Event.product_id)
        .order_by(func.count(func.distinct(Event.session_id)).desc())
        .limit(limit)
    )
    ids = [pid for pid, _ in db.execute(ranked).all()]

    if ids:
        by_id = {
            p.id: p
            for p in db.scalars(
                select(Product).where(Product.id.in_(ids), Product.is_active.is_(True))
            )
        }
        found = [by_id[pid] for pid in ids if pid in by_id]
        if found:
            return found

    return list(
        db.scalars(
            select(Product)
            .where(
                Product.category == product.category,
                Product.id != product.id,
                Product.is_active.is_(True),
            )
            .order_by(Product.enrollments.desc())
            .limit(limit)
        )
    )


def is_enrolled(db: Session, audience: Audience, product_id: int) -> bool:
    from app.service import owns_enrollment

    if not audience.is_valid:
        return False
    return (
        db.scalar(
            select(Enrollment.id).where(
                owns_enrollment(audience), Enrollment.product_id == product_id
            )
        )
        is not None
    )


def signal_context(db: Session, user: User | None, session_id: str = "") -> dict:
    """First-paint state for the live Signal panel.

    Server-rendered so the panel is populated before Alpine runs and with
    JavaScript disabled; the SSE stream takes over from there.

    Guests get the same treatment as accounts — same profile, same gates, same
    agent — because their behaviour is tracked identically.
    """
    audience = Audience.of(user, session_id)
    if not audience.is_valid:
        return {}
    return {
        "signal_status": recommendation_status(db, audience),
        # Scopes the browser-side feed, so signing in or out never inherits
        # the previous visitor's activity on a shared machine.
        "signal_owner": audience.key,
    }


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
        current = triggers.current_recommendation(db, Audience.of(user, get_session_id(request)))
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
        db=db,
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
        db=db,
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
    products = _search_products(db, q) if q.strip() else []

    return render(
        request,
        "search.html",
        user,
        {
            "q": q,
            "products": products,
            "categories": _categories(db),
            **signal_context(db, user, get_session_id(request)),
        },
        db=db,
    )


def _search_products(db: Session, q: str, limit: int = 24) -> list[Product]:
    pattern = f"%{q.strip()}%"
    return list(
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
            .limit(limit)
        )
    )


@router.get("/api/search", response_class=HTMLResponse)
def search_fragment(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
):
    """Results only, as an HTML fragment, for the live search box.

    Returns markup rather than JSON so the results are rendered by the same
    template as the full page — one definition of a course card, and the
    tracking data attributes come along for free.
    """
    products = _search_products(db, q) if q.strip() else []
    return render(
        request, "_search_results.html", user, {"q": q, "products": products}, db=db
    )


@router.get("/api/search/suggest", response_class=HTMLResponse)
def search_suggestions(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
):
    """Compact results for the header search dropdown.

    Deliberately a different, smaller partial from the results grid — a course
    card is far too tall to stack six of under a header input.
    """
    products = _search_products(db, q, limit=6) if len(q.strip()) >= 2 else []
    return render(
        request, "_search_suggestions.html", user, {"q": q, "products": products}, db=db
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
            request, "not_found.html", user, {"categories": _categories(db)},
            db=db, status_code=404,
        )

    return render(
        request,
        "product.html",
        user,
        {
            "product": product,
            "related": also_explored(db, product),
            "enrolled": is_enrolled(db, Audience.of(user, get_session_id(request)), product.id),
            "in_cart": product.id in cart_product_ids(db, get_session_id(request)),
            "instructor": instructor_profile(product.instructor),
            "instructor_initials": initials(product.instructor),
            "categories": _categories(db),
            **signal_context(db, user, get_session_id(request)),
        },
        db=db,
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user_optional),
):
    audience = Audience.of(user, get_session_id(request))
    if not audience.is_valid:
        return login_redirect("/dashboard")

    history = [
        {"version": r.version, "headline": r.headline, "created_at": r.created_at}
        for r in db.scalars(
            select(Recommendation)
            .where(triggers.owns(audience))
            .order_by(Recommendation.created_at.desc())
            .limit(6)
        )
    ]
    owned = list(
        db.scalars(
            select(Product)
            .join(Enrollment, Enrollment.product_id == Product.id)
            .where(owns_enrollment(audience))
            .order_by(Enrollment.created_at.desc())
        )
    )

    return render(
        request,
        "dashboard.html",
        user,
        {
            "status": recommendation_status(db, audience),
            "history": history,
            "enrolled": owned,
            "categories": _categories(db),
        },
    )
