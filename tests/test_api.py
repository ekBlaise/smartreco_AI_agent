"""Pages, auth, role enforcement and the admin dual-write path."""

from __future__ import annotations

from app.audience import Audience

from sqlalchemy import select

from app.models import Product, User
from app.vector import store
from tests.test_triggers import AGENTIC_SESSION, add_events


# --- pages ------------------------------------------------------------------

def test_public_pages_render(client, catalog):
    for path in ["/", "/catalog", "/search?q=agents", "/login", "/register"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "SmartReco" in response.text


def test_product_page_renders_and_carries_tracking_hooks(client, catalog):
    response = client.get(f"/course/{catalog[0].slug}")

    assert response.status_code == 200
    assert catalog[0].title in response.text
    assert f'data-product-id="{catalog[0].id}"' in response.text
    assert "data-track-view" in response.text
    assert "/static/js/tracker.js" in response.text


def test_unknown_product_is_404_not_500(client, catalog):
    assert client.get("/course/does-not-exist").status_code == 404


def test_catalog_filters_narrow_the_results(client, catalog):
    response = client.get("/catalog?category=Data+Engineering")

    assert response.status_code == 200
    assert "Streaming Data with Kafka" in response.text
    assert "Building AI Agents with LangGraph" not in response.text


def test_search_finds_by_keyword(client, catalog):
    response = client.get("/search?q=kafka")

    assert response.status_code == 200
    assert "Streaming Data with Kafka" in response.text


# --- auth -------------------------------------------------------------------

def test_the_dashboard_works_for_a_guest(client, catalog):
    """Guests are tracked and get recommendations, so they get the page too —
    keyed by session instead of account."""
    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 200
    assert "For you" in response.text


def test_register_then_land_on_the_dashboard(client, catalog):
    response = client.post(
        "/register",
        data={"email": "new@test.dev", "full_name": "New Learner", "password": "supersecret"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert client.get("/dashboard").status_code == 200


def test_registration_rejects_a_weak_password(client):
    response = client.post(
        "/register", data={"email": "weak@test.dev", "full_name": "", "password": "short"}
    )

    assert response.status_code == 400
    assert "8 characters" in response.text


def test_duplicate_email_is_rejected(client, learner):
    response = client.post(
        "/register", data={"email": learner.email, "full_name": "", "password": "supersecret"}
    )

    assert response.status_code == 409


def test_bad_credentials_are_rejected(client, learner):
    response = client.post("/login", data={"email": learner.email, "password": "wrong-password"})

    assert response.status_code == 401
    assert "Incorrect email or password" in response.text


def test_passwords_are_never_stored_in_the_clear(client, db):
    client.post(
        "/register",
        data={"email": "hash@test.dev", "full_name": "", "password": "supersecret"},
    )
    user = db.scalar(select(User).where(User.email == "hash@test.dev"))

    assert user.password_hash != "supersecret"
    assert user.password_hash.startswith("$2b$")


def test_logout_clears_the_session(logged_in, learner):
    """After signing out the dashboard is no longer *their* dashboard — it
    falls back to the anonymous session's own recommendations."""
    before = logged_in.get("/dashboard").text
    assert 'href="/logout"' in before, "signed in"

    logged_in.get("/logout", follow_redirects=False)

    after = logged_in.get("/dashboard").text
    assert 'href="/logout"' not in after, "signed out"
    assert 'href="/login"' in after


def test_login_redirect_only_targets_this_site(client, learner):
    """An open-redirect attempt must be ignored."""
    response = client.post(
        "/login",
        data={"email": learner.email, "password": "learner1234", "next": "https://evil.example"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/dashboard"


# --- role enforcement -------------------------------------------------------

def test_a_normal_user_cannot_reach_the_admin_area(logged_in, catalog):
    assert logged_in.get("/admin/products").status_code == 403
    assert logged_in.get("/admin/agent-runs").status_code == 403


def test_a_normal_user_cannot_create_products(logged_in):
    response = logged_in.post(
        "/admin/products", data={"title": "Sneaky Course", "category": "Hacking"}
    )
    assert response.status_code == 403


def test_a_signed_out_visitor_is_sent_to_login(client):
    response = client.get("/admin/products", follow_redirects=False)

    assert response.status_code == 303
    assert "/login" in response.headers["location"]


# --- admin CRUD dual-write --------------------------------------------------

def _login_admin(client, admin):
    response = client.post(
        "/login",
        data={"email": admin.email, "password": "admin1234", "next": "/admin/products"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


def test_admin_create_writes_to_both_stores(client, admin, catalog, db):
    _login_admin(client, admin)
    before = store.count_points()

    response = client.post(
        "/admin/products",
        data={
            "title": "Vector Search for Recommendations",
            "description": "Embeddings, similarity and ranking for product recommendation systems.",
            "category": "Vector Databases",
            "level": "intermediate",
            "price": "129",
            "instructor": "Test Author",
            "duration_hours": "8",
            "rating": "4.6",
            "tags": "vectors, ranking, embeddings",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    product = db.scalar(select(Product).where(Product.slug == "vector-search-for-recommendations"))
    assert product is not None
    assert product.vector_in_sync
    assert store.count_points() == before + 1

    # And it is immediately retrievable semantically.
    from conftest import fake_embedding

    hits = store.search(fake_embedding("embeddings similarity ranking recommendation"), limit=5)
    assert product.id in [h.product_id for h in hits]


def test_admin_edit_keeps_the_stores_consistent(client, admin, catalog, db):
    _login_admin(client, admin)
    product = catalog[0]

    response = client.post(
        f"/admin/products/{product.id}",
        data={
            "title": product.title,
            "description": "Rewritten to cover kafka partitions and streaming consumers.",
            "category": product.category,
            "level": "advanced",
            "price": "199",
            "instructor": product.instructor,
            "duration_hours": "12",
            "rating": "4.9",
            "tags": "kafka, streaming",
            "is_active": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    db.expire_all()
    product = db.get(Product, product.id)
    assert product.level == "advanced"
    assert product.vector_in_sync

    point = store.get_client().retrieve(
        collection_name=store.settings.qdrant_collection, ids=[product.id], with_payload=True
    )[0]
    assert point.payload["level"] == "advanced"
    assert point.payload["price"] == 199.0


def test_admin_delete_removes_from_both_stores(client, admin, catalog, db):
    _login_admin(client, admin)
    product_id = catalog[0].id
    before = store.count_points()

    response = client.post(f"/admin/products/{product_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    db.expire_all()
    assert db.get(Product, product_id) is None
    assert store.count_points() == before - 1


def test_admin_can_see_agent_runs(client, admin, catalog, db, learner):
    from app.service import generate_recommendation

    add_events(db, learner, catalog, *AGENTIC_SESSION)
    generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    _login_admin(client, admin)
    response = client.get("/admin/agent-runs")

    assert response.status_code == 200
    assert "profile_behavior" in response.text


# --- recommendation API -----------------------------------------------------

def test_the_recommendation_api_answers_a_guest(client, catalog):
    """No 401: a signed-out visitor has a profile of their own."""
    response = client.get("/api/recommendations")

    assert response.status_code == 200
    assert response.json()["status"] in {
        "insufficient_activity", "pending", "ready", "unavailable",
    }


def test_api_explains_why_there_is_nothing_yet(logged_in, catalog):
    response = logged_in.get("/api/recommendations")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_activity"
    assert "more tracked action" in body["message"]
    assert body["recommendation"] is None


def test_api_returns_the_stored_recommendation(logged_in, catalog, db, learner):
    from app.service import generate_recommendation

    add_events(db, learner, catalog, *AGENTIC_SESSION)
    generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    response = logged_in.get("/api/recommendations")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["recommendation"]["headline"]
    assert body["recommendation"]["items"]
    for item in body["recommendation"]["items"]:
        assert item["why_this"]
        assert item["slug"]


def test_dashboard_renders_the_recommendation(logged_in, catalog, db, learner):
    from app.service import generate_recommendation

    add_events(db, learner, catalog, *AGENTIC_SESSION)
    recommendation, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    response = logged_in.get("/dashboard")

    assert response.status_code == 200
    assert recommendation.headline in response.text
    assert recommendation.items[0].product.title in response.text


def test_dashboard_alpine_attributes_are_well_formed(logged_in, catalog, db, learner):
    """A double quote inside a double-quoted x-data would break Alpine silently.

    When that happened the server-rendered HTML still contained the whole
    recommendation — only the browser hid it — so nothing else in this file
    would have caught it.
    """
    from app.service import generate_recommendation

    add_events(db, learner, catalog, *AGENTIC_SESSION)
    generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    html = logged_in.get("/dashboard").text

    assert 'x-data="recoPanel(&#39;' in html or "x-data=\"recoPanel('" in html
    assert 'recoPanel("' not in html, "unescaped quote truncates the x-data attribute"


def test_healthz_reports_the_stack(client):
    body = client.get("/healthz").json()

    assert body["ok"] is True
    assert body["mesh_configured"] is True
    assert body["vector_store"]["ok"] is True


def test_the_panel_says_which_kind_of_waiting_it_is(logged_in, db, learner, catalog):
    """'The agent is reading your activity' for a user the gates have declined
    reads as a spinner that never resolves. Below threshold must say so."""
    from app.audience import Audience
    from app.service import recommendation_status
    from tests.test_triggers import add_events

    # Enough events to be past reco_min_events, but well short of the score.
    add_events(db, learner, catalog, ("product_view", 0, None), ("page_view", None, None),
               ("page_view", None, None), ("scroll_depth", None, None))
    db.commit()

    status = recommendation_status(db, Audience(user_id=learner.id))

    assert status["status"] == "insufficient_activity"
    assert "more point" in status["message"]
    assert "reading your activity" not in status["message"]
