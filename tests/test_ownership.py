"""Nobody is handed data that is not theirs.

Every read is scoped by `Audience` rather than by an id taken from the request,
so there is no id to tamper with in the first place. These pin that: the same
endpoints, two different people, no bleed either way.
"""

from __future__ import annotations

from sqlalchemy import select

from app.audience import Audience
from app.models import CartItem, Enrollment, User
from app.security import hash_password
from app.service import generate_recommendation, recommendation_status
from tests.test_triggers import AGENTIC_SESSION, add_events


def _other_learner(db) -> User:
    user = User(
        email="other@test.dev",
        full_name="Other Learner",
        password_hash=hash_password("learner1234"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# --- recommendations ---------------------------------------------------------

def test_one_learners_recommendation_is_invisible_to_another(db, learner, catalog, fake_mesh):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()
    mine, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    stranger = _other_learner(db)
    theirs = recommendation_status(db, Audience(user_id=stranger.id))

    assert mine is not None
    assert theirs["recommendation"] is None
    assert theirs["events_tracked"] == 0, "another account's activity is not counted"


def test_the_api_answers_with_the_callers_own_recommendation(client, db, learner, catalog, fake_mesh):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()
    mine, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    stranger = _other_learner(db)
    client.post(
        "/login",
        data={"email": stranger.email, "password": "learner1234", "next": "/dashboard"},
        follow_redirects=False,
    )

    body = client.get("/api/recommendations").json()

    assert body["recommendation"] is None
    assert mine.headline not in client.get("/dashboard").text


def test_a_guest_session_cannot_read_an_accounts_recommendation(client, db, learner, catalog, fake_mesh):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()
    mine, _ = generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    # A brand-new browser, never signed in.
    body = client.get("/api/recommendations").json()

    assert body["recommendation"] is None
    assert mine is not None


def test_signing_out_stops_showing_the_previous_person(client, db, learner, catalog, fake_mesh):
    """The anon session id rotates on logout, so the next visitor on this
    browser starts from zero instead of inheriting the last one's signal."""
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()
    generate_recommendation(db, Audience(user_id=learner.id))
    db.commit()

    client.post(
        "/login",
        data={"email": learner.email, "password": "learner1234", "next": "/dashboard"},
        follow_redirects=False,
    )
    assert client.get("/api/recommendations").json()["recommendation"] is not None

    client.get("/logout", follow_redirects=False)

    after = client.get("/api/recommendations").json()
    assert after["recommendation"] is None
    assert after["events_tracked"] == 0


# --- carts and enrolments ----------------------------------------------------

def test_a_cart_belongs_to_its_session_only(client, catalog, db):
    client.post(f"/api/cart/{catalog[0].id}")

    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app) as other_browser:
        assert other_browser.get("/cart").text.count(catalog[0].title) == 0
        assert len(db.scalars(select(CartItem)).all()) == 1


def test_removing_from_a_cart_cannot_touch_someone_elses(client, catalog, db):
    """The delete is scoped by session, so a guessed product id is inert."""
    client.post(f"/api/cart/{catalog[0].id}")

    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app) as attacker:
        attacker.request("DELETE", f"/api/cart/{catalog[0].id}")

    assert len(db.scalars(select(CartItem)).all()) == 1, "the owner's cart is untouched"


def test_enrolments_are_scoped_to_the_owner(db, learner, catalog):
    stranger = _other_learner(db)
    db.add(Enrollment(user_id=learner.id, product_id=catalog[0].id))
    db.commit()

    from app.service import enrolled_product_ids

    assert enrolled_product_ids(db, Audience(user_id=learner.id)) == [catalog[0].id]
    assert enrolled_product_ids(db, Audience(user_id=stranger.id)) == []


def test_the_dashboard_never_lists_another_accounts_courses(client, db, learner, catalog):
    db.add(Enrollment(user_id=learner.id, product_id=catalog[0].id))
    db.commit()
    stranger = _other_learner(db)

    client.post(
        "/login",
        data={"email": stranger.email, "password": "learner1234", "next": "/dashboard"},
        follow_redirects=False,
    )

    assert catalog[0].title not in client.get("/dashboard").text


# --- account creation --------------------------------------------------------

def test_registration_rejects_a_password_mismatch(client):
    """Enforced server-side: `required` and `minlength` only bind a browser."""
    response = client.post(
        "/register",
        data={
            "email": "mismatch@test.dev",
            "full_name": "",
            "password": "supersecret",
            "confirm_password": "supersecret-typo",
        },
    )

    assert response.status_code == 400
    assert "do not match" in response.text


def test_registration_still_rejects_a_short_password(client):
    response = client.post(
        "/register",
        data={
            "email": "short@test.dev", "full_name": "",
            "password": "short", "confirm_password": "short",
        },
    )

    assert response.status_code == 400
    assert "8 characters" in response.text


def test_checkout_signup_rejects_a_mismatch_and_keeps_the_cart(client, catalog, db):
    client.post(f"/api/cart/{catalog[0].id}")

    response = client.post(
        "/cart/checkout",
        data={
            "email": "co-mismatch@test.dev",
            "password": "supersecret",
            "confirm_password": "different",
        },
        follow_redirects=False,
    )

    assert "do%20not%20match" in response.headers["location"]
    assert db.scalar(select(User).where(User.email == "co-mismatch@test.dev")) is None
    assert db.scalars(select(CartItem)).all(), "the cart survives a failed signup"


def test_matching_passwords_create_the_account(client, catalog, db):
    client.post(f"/api/cart/{catalog[0].id}")

    client.post(
        "/cart/checkout",
        data={
            "email": "co-ok@test.dev",
            "password": "supersecret",
            "confirm_password": "supersecret",
        },
        follow_redirects=False,
    )

    account = db.scalar(select(User).where(User.email == "co-ok@test.dev"))
    assert account is not None
    # ...and the enrolment landed on it, not on the guest session.
    owned = db.scalar(select(Enrollment).where(Enrollment.product_id == catalog[0].id))
    db.refresh(owned)
    assert owned.user_id == account.id
