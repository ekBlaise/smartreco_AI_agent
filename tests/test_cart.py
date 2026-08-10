"""Cart and anonymous behaviour.

A marketplace cannot put a sign-in wall in front of its primary action, and it
cannot pretend it is not watching anonymous visitors when it is. These cover
both: adding to a cart signed out, and the Signal panel working without an
account.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import CartItem, Enrollment, Event


# --- adding to a cart, signed out --------------------------------------------

def test_anyone_can_add_to_a_cart(client, catalog, db):
    response = client.post(f"/api/cart/{catalog[0].id}")

    assert response.status_code == 200
    assert response.json()["in_cart"] is True
    assert response.json()["count"] == 1
    assert db.scalar(select(CartItem).where(CartItem.product_id == catalog[0].id))


def test_adding_twice_is_not_an_error(client, catalog, db):
    client.post(f"/api/cart/{catalog[0].id}")
    second = client.post(f"/api/cart/{catalog[0].id}")

    assert second.status_code == 200
    assert second.json()["count"] == 1
    rows = db.scalars(select(CartItem).where(CartItem.product_id == catalog[0].id)).all()
    assert len(rows) == 1


def test_adding_to_the_cart_is_tracked_as_intent(client, catalog, db, fake_redis):
    """Putting something in a basket is the strongest signal short of buying."""
    from app.ingest.buffer import flush_buffer

    client.post(f"/api/cart/{catalog[0].id}")
    flush_buffer()

    event = db.scalar(select(Event).where(Event.type == "enroll_intent"))
    assert event is not None
    assert event.product_id == catalog[0].id
    assert event.meta.get("surface") == "add_to_cart"


def test_removing_from_the_cart(client, catalog, db):
    client.post(f"/api/cart/{catalog[0].id}")

    response = client.request("DELETE", f"/api/cart/{catalog[0].id}")

    assert response.json() == {"in_cart": False, "count": 0, "total": 0, "product_ids": []}


def test_adding_a_missing_course_is_a_404(client):
    assert client.post("/api/cart/999999").status_code == 404


def test_the_cart_page_renders_signed_out(client, catalog):
    client.post(f"/api/cart/{catalog[0].id}")

    html = client.get("/cart").text

    assert catalog[0].title in html
    assert "Complete enrollment" in html, "no sign-in wall at checkout"
    assert "Create an account so this follows me" in html, "the account is offered, not required"


def test_checkout_can_create_the_account_on_the_way_through(client, catalog, db):
    """Making an account is part of checking out, not a gate in front of it."""
    from app.models import User

    client.post(f"/api/cart/{catalog[0].id}")

    response = client.post(
        "/cart/checkout",
        data={"email": "checkout@test.dev", "password": "supersecret", "full_name": "Cee Out"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    account = db.scalar(select(User).where(User.email == "checkout@test.dev"))
    assert account is not None

    owned = db.scalar(select(Enrollment).where(Enrollment.product_id == catalog[0].id))
    db.refresh(owned)
    assert owned.user_id == account.id, "the enrolment lands on the new account"
    # ...and they are signed in afterwards.
    assert "smartreco_session" in response.headers.get("set-cookie", "")


def test_checkout_rejects_a_duplicate_email_without_losing_the_cart(
    client, catalog, db, learner
):
    client.post(f"/api/cart/{catalog[0].id}")

    response = client.post(
        "/cart/checkout",
        data={"email": learner.email, "password": "supersecret"},
        follow_redirects=False,
    )

    assert "already%20registered" in response.headers["location"]
    assert db.scalars(select(CartItem)).all(), "the cart survives a failed signup"


def test_an_empty_cart_says_so(client):
    assert "Your cart is empty" in client.get("/cart").text


# --- checkout -----------------------------------------------------------------

def test_checkout_works_without_an_account(client, catalog, db):
    """No sign-in wall at all: a guest's enrolments are keyed by session,
    exactly like their behaviour and their recommendations."""
    client.post(f"/api/cart/{catalog[0].id}")

    response = client.post("/cart/checkout", follow_redirects=False)

    assert response.status_code == 303
    enrollment = db.scalar(select(Enrollment).where(Enrollment.product_id == catalog[0].id))
    assert enrollment is not None
    assert enrollment.user_id is None and enrollment.session_id
    assert db.scalars(select(CartItem)).all() == [], "cart is cleared on checkout"


def test_signing_in_claims_what_you_did_as_a_guest(client, catalog, db, learner):
    """Otherwise everything before the account silently belongs to a session
    nobody can be identified by again."""
    client.post(f"/api/cart/{catalog[0].id}")
    client.post("/cart/checkout", follow_redirects=False)

    client.post(
        "/login",
        data={"email": learner.email, "password": "learner1234", "next": "/dashboard"},
        follow_redirects=False,
    )

    claimed = db.scalar(select(Enrollment).where(Enrollment.product_id == catalog[0].id))
    db.refresh(claimed)
    assert claimed.user_id == learner.id
    assert claimed.session_id is None


def test_checkout_turns_the_cart_into_enrollments(logged_in, catalog, db, learner):
    logged_in.post(f"/api/cart/{catalog[0].id}")
    logged_in.post(f"/api/cart/{catalog[1].id}")

    response = logged_in.post("/cart/checkout", follow_redirects=False)

    assert response.status_code == 303
    enrolled = {
        e.product_id
        for e in db.scalars(select(Enrollment).where(Enrollment.user_id == learner.id))
    }
    assert enrolled == {catalog[0].id, catalog[1].id}
    assert db.scalars(select(CartItem)).all() == [], "cart is cleared on checkout"


def test_checkout_skips_courses_already_owned(logged_in, catalog, db, learner):
    db.add(Enrollment(user_id=learner.id, product_id=catalog[0].id))
    db.commit()
    logged_in.post(f"/api/cart/{catalog[0].id}")

    logged_in.post("/cart/checkout", follow_redirects=False)

    rows = db.scalars(
        select(Enrollment).where(
            Enrollment.user_id == learner.id, Enrollment.product_id == catalog[0].id
        )
    ).all()
    assert len(rows) == 1, "checking out an owned course must not duplicate it"


def test_checkout_with_an_empty_cart_is_harmless(logged_in):
    response = logged_in.post("/cart/checkout", follow_redirects=False)

    assert response.status_code == 303


# --- the course page ----------------------------------------------------------

def test_the_course_page_offers_add_to_cart_signed_out(client, catalog):
    """The old sign-in wall stood in front of the only action that matters."""
    html = client.get(f"/course/{catalog[0].slug}").text

    assert "Add to cart" in html
    assert "Sign in to enroll" not in html


def test_the_course_page_knows_what_is_already_in_the_cart(client, catalog):
    client.post(f"/api/cart/{catalog[0].id}")

    html = client.get(f"/course/{catalog[0].slug}").text

    assert f"buyCard({catalog[0].id}, false, true)" in html


def test_the_header_shows_a_cart_count(client, catalog):
    assert 'class="cart-count"' not in client.get("/catalog").text

    client.post(f"/api/cart/{catalog[0].id}")

    assert 'class="cart-count"' in client.get("/catalog").text


# --- the Signal panel without an account --------------------------------------

def test_the_signal_panel_renders_for_anonymous_visitors(client, catalog):
    """Their behaviour is genuinely tracked, so hiding the panel from them was
    showing nothing to exactly the people still being convinced."""
    html = client.get(f"/course/{catalog[0].slug}").text

    assert "Your Signal" in html
    assert '"owner": "s:' in html, "a guest feed is scoped to their session"


def test_the_anonymous_panel_counts_that_session_s_events(client, catalog, db, fake_redis):
    from app.ingest.buffer import flush_buffer

    client.post(
        "/api/events/batch",
        json={"events": [
            {"type": "product_view", "product_id": catalog[0].id},
            {"type": "search", "query": "agent memory"},
        ]},
    )
    flush_buffer()

    html = client.get(f"/course/{catalog[0].slug}").text

    assert "2 actions observed" in html


def test_a_signed_in_panel_is_scoped_to_the_account(logged_in, catalog, learner):
    """The browser-side feed is wiped when the owner changes, so signing in or
    out never inherits the previous visitor's activity."""
    html = logged_in.get(f"/course/{catalog[0].slug}").text

    assert f'"owner": "u:{learner.id}"' in html


# --- static assets ------------------------------------------------------------

def test_static_assets_are_cache_busted(client):
    """Without this a returning visitor keeps the JS their browser cached, and
    a deploy reaches new visitors only."""
    html = client.get("/").text

    assert "/static/js/signal.js?v=" in html
    assert "/static/css/app.css?v=" in html
