"""Course page depth, live search, and the two reported bugs."""

from __future__ import annotations

from app.api.routes.pages import also_explored
from app.models import Event, Product
from app.vector import sync


# --- regression: a failed vector write must not report success ---------------

def test_a_failed_vector_write_is_reported_as_a_failure(admin_client, monkeypatch, db):
    """`sync_products` returns a failure count instead of raising.

    Catching exceptions alone reported "indexed it for semantic search" while
    the product sat unindexed and invisible to every recommendation.
    """
    def refuse(_texts, use_cache=True):
        raise RuntimeError("Error code: 402 - spend_limit_exceeded")

    monkeypatch.setattr(sync, "embed_texts", refuse)

    response = admin_client.post(
        "/admin/products",
        data={
            "title": "Unindexable Course", "description": "x", "category": "Agentic AI",
            "level": "beginner", "price": "10", "instructor": "QA",
            "duration_hours": "1", "rating": "4", "tags": "t", "is_active": "true",
        },
        follow_redirects=True,
    )

    assert "NOT searchable" in response.text
    assert "indexed it for semantic search" not in response.text
    # ...and it says what to actually do about a billing refusal.
    assert "credit" in response.text

    product = db.query(Product).filter_by(title="Unindexable Course").one()
    assert product.vector_sync_error
    assert product.id is not None, "the SQL row is still saved for the reconciler"


def test_a_successful_vector_write_still_reports_success(admin_client):
    response = admin_client.post(
        "/admin/products",
        data={
            "title": "Indexable Course", "description": "x", "category": "Agentic AI",
            "level": "beginner", "price": "10", "instructor": "QA",
            "duration_hours": "1", "rating": "4", "tags": "t", "is_active": "true",
        },
        follow_redirects=True,
    )

    assert "indexed it for semantic search" in response.text
    assert "NOT searchable" not in response.text


# --- regression: admin pages must answer in HTML -----------------------------

def test_a_signed_in_learner_gets_a_page_not_a_json_error(logged_in):
    """Raising HTTPException dumped raw JSON into the browser with no way out."""
    response = logged_in.get("/admin")

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "needs an admin account" in response.text
    assert '{"detail"' not in response.text
    # ...and offers a route forward rather than a dead end.
    assert "/logout?next=" in response.text


def test_logout_can_carry_you_to_a_sign_in_for_where_you_were_going(client):
    response = client.get("/logout?next=/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/admin"


def test_logout_refuses_an_offsite_redirect(client):
    response = client.get("/logout?next=https://evil.test/x", follow_redirects=False)

    assert "evil.test" not in response.headers["location"]


def test_the_login_page_lists_both_demo_accounts(client):
    html = client.get("/login").text

    assert "user@smartreco.dev" in html
    assert "admin@smartreco.dev" in html


# --- course page depth -------------------------------------------------------

def test_the_course_page_shows_curriculum_and_instructor(client, db, catalog):
    product = catalog[0]
    product.curriculum = ["State machines for agents", "Ship: a production agent"]
    product.instructor = "Priya Raman"
    db.commit()

    html = client.get(f"/course/{product.slug}").text

    assert "Curriculum" in html
    assert "State machines for agents" in html
    assert "Priya Raman" in html
    assert "Founding Engineer, agent tooling" in html  # from the instructor lookup


def test_an_unknown_instructor_still_renders(client, db, catalog):
    """Admin-created courses have instructors with no profile on file."""
    product = catalog[0]
    product.instructor = "Someone Not In The Lookup"
    db.commit()

    response = client.get(f"/course/{product.slug}")

    assert response.status_code == 200
    assert "Someone Not In The Lookup" in response.text


def test_curriculum_is_part_of_what_gets_embedded(db, catalog):
    """Module titles often match how a learner searches, so they must be indexed."""
    product = catalog[0]
    product.curriculum = ["Durable checkpoints and resuming"]

    assert "Durable checkpoints and resuming" in product.embedding_document


def test_editing_the_curriculum_requires_a_new_embedding(db, catalog):
    product = catalog[0]
    before = product.content_digest
    product.curriculum = ["A brand new module"]

    assert product.content_digest != before
    assert product.needs_embedding


# --- "students who explored this also looked at" -----------------------------

def test_also_explored_uses_real_co_viewing(db, catalog):
    anchor, together, unrelated = catalog[0], catalog[1], catalog[2]
    for session_id in ("s1", "s2"):
        db.add(Event(session_id=session_id, type="product_view", product_id=anchor.id))
        db.add(Event(session_id=session_id, type="product_view", product_id=together.id))
    db.add(Event(session_id="s3", type="product_view", product_id=unrelated.id))
    db.commit()

    result = also_explored(db, anchor)

    assert result[0].id == together.id
    assert anchor.id not in [p.id for p in result], "never recommends itself"


def test_also_explored_falls_back_when_nothing_was_co_viewed(db, catalog):
    """A brand-new course has no co-view history; the section still renders."""
    anchor = catalog[0]

    result = also_explored(db, anchor)

    assert result, "falls back to the same category rather than showing nothing"
    assert all(p.id != anchor.id for p in result)


# --- live search -------------------------------------------------------------

def test_search_fragment_returns_results_markup_only(client, catalog):
    response = client.get(f"/api/search?q={catalog[0].title.split()[0]}")

    assert response.status_code == 200
    assert catalog[0].title in response.text
    assert "<html" not in response.text.lower(), "a fragment, not a whole page"


def test_search_fragment_handles_an_empty_query(client):
    response = client.get("/api/search?q=")

    assert response.status_code == 200
    assert "Type a topic" in response.text


def test_search_fragment_says_so_when_nothing_matches(client, catalog):
    response = client.get("/api/search?q=zzzznotacourse")

    assert response.status_code == 200
    assert "Nothing matched" in response.text


def test_header_suggestions_return_compact_rows(client, catalog):
    target = catalog[0]

    response = client.get(f"/api/search/suggest?q={target.title.split()[0]}")

    assert response.status_code == 200
    assert target.title in response.text
    assert "suggest-item" in response.text
    assert "<html" not in response.text.lower(), "a fragment, not a whole page"


def test_header_suggestions_stay_quiet_for_one_character(client, catalog):
    """One letter matches most of the catalog and is never a real intent."""
    assert client.get("/api/search/suggest?q=a").text.strip() == ""
    assert client.get("/api/search/suggest?q=").text.strip() == ""


def test_header_suggestions_are_capped(client, catalog):
    """The dropdown hangs under a header input; it cannot grow unbounded."""
    response = client.get("/api/search/suggest?q=e")  # too short -> empty
    assert response.text.strip() == ""

    response = client.get("/api/search/suggest?q=a" * 1)
    assert response.text.count("suggest-item") <= 6


def test_header_suggestions_offer_a_way_to_the_full_results(client, catalog):
    response = client.get(f"/api/search/suggest?q={catalog[0].title.split()[0]}")

    assert "/search?q=" in response.text


def test_header_suggestions_say_so_when_nothing_matches(client, catalog):
    response = client.get("/api/search/suggest?q=zzzznotacourse")

    assert "Nothing matched" in response.text


def test_header_suggestions_carry_tracking_attributes(client, catalog):
    """A click from the dropdown is behaviour like any other and must be seen."""
    html = client.get(f"/api/search/suggest?q={catalog[0].title.split()[0]}").text

    assert 'data-track-click="product_click"' in html
    assert 'data-track-surface="header_suggest"' in html


def test_the_header_search_still_submits_without_javascript(client):
    html = client.get("/").text

    assert 'action="/search"' in html
    assert 'name="q"' in html


def test_admin_table_fragment_filters_by_title(admin_client, catalog):
    target = catalog[0]

    response = admin_client.get(f"/admin/products/table?q={target.title.split()[0]}")

    assert response.status_code == 200
    assert target.title in response.text
    assert "<html" not in response.text.lower(), "a fragment, not a whole page"


def test_admin_table_fragment_keeps_the_edit_bindings(admin_client, catalog):
    """Rows are injected via innerHTML, so they must arrive with their Alpine
    bindings intact or Edit silently stops working after a filter."""
    html = admin_client.get("/admin/products/table").text

    assert "@click='editing =" in html
    assert '"curriculum":' in html


def test_admin_table_fragment_says_so_when_nothing_matches(admin_client, catalog):
    response = admin_client.get("/admin/products/table?q=zzzznotacourse")

    assert "No product matches" in response.text


def test_admin_table_fragment_is_admin_only(logged_in):
    response = logged_in.get("/admin/products/table")

    assert response.status_code == 403


def test_the_admin_filter_still_works_without_javascript(admin_client, catalog):
    """The filter is a real GET form; the full page must filter server-side."""
    target = catalog[0]

    html = admin_client.get(f"/admin/products?q={target.title.split()[0]}").text

    assert target.title in html
    assert 'action="/admin/products"' in html


def test_the_search_page_still_works_without_javascript(client, catalog):
    """The fragment is an enhancement; the plain form submit must still render."""
    html = client.get(f"/search?q={catalog[0].title.split()[0]}").text

    assert catalog[0].title in html
    assert 'action="/search"' in html
