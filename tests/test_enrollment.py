"""Enrolment: the button does something, and the agent respects the result."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Enrollment, Event
from app.service import generate_recommendation
from tests.test_triggers import AGENTIC_SESSION, add_events


# --- the endpoint ------------------------------------------------------------

def test_enrolling_persists_and_reports_state(logged_in, db, learner, catalog):
    course = catalog[0]

    response = logged_in.post(f"/api/enroll/{course.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["enrolled"] is True and body["created"] is True
    assert course.title in body["message"]

    stored = db.scalar(
        select(Enrollment).where(
            Enrollment.user_id == learner.id, Enrollment.product_id == course.id
        )
    )
    assert stored is not None


def test_enrolling_twice_is_not_an_error(logged_in, db, learner, catalog):
    """A double click, or a second tab, must not 500 or duplicate the row."""
    course = catalog[0]

    first = logged_in.post(f"/api/enroll/{course.id}")
    second = logged_in.post(f"/api/enroll/{course.id}")

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["enrolled"] is True

    rows = db.scalars(
        select(Enrollment).where(
            Enrollment.user_id == learner.id, Enrollment.product_id == course.id
        )
    ).all()
    assert len(rows) == 1


def test_enrolling_records_the_behavioural_signal(logged_in, db, learner, catalog, fake_redis):
    """Enrolment is the strongest signal there is; the agent must see it."""
    from app.ingest.buffer import flush_buffer

    logged_in.post(f"/api/enroll/{catalog[0].id}")
    flush_buffer()

    event = db.scalar(
        select(Event).where(Event.user_id == learner.id, Event.type == "enroll_intent")
    )
    assert event is not None
    assert event.product_id == catalog[0].id
    assert event.weight >= 5, "enrolment must outweigh a page view"


def test_enrolling_bumps_the_public_enrollment_count(logged_in, db, catalog):
    course = catalog[0]
    before = course.enrollments or 0

    logged_in.post(f"/api/enroll/{course.id}")
    db.refresh(course)

    assert course.enrollments == before + 1


def test_enrolling_requires_a_session(client, catalog):
    """Anonymous must get a clean 401, which the button turns into a sign-in."""
    response = client.post(f"/api/enroll/{catalog[0].id}")

    assert response.status_code == 401


def test_enrolling_in_a_missing_course_is_a_404(logged_in):
    assert logged_in.post("/api/enroll/999999").status_code == 404


# --- the page ----------------------------------------------------------------

def test_the_button_reflects_an_existing_enrollment(logged_in, db, learner, catalog):
    course = catalog[0]
    db.add(Enrollment(user_id=learner.id, product_id=course.id))
    db.commit()

    html = logged_in.get(f"/course/{course.slug}").text

    assert f"buyCard({course.id}, true, false)" in html
    assert "Enrolled" in html
    assert "Add to cart" not in html, "an owned course is not for sale again"


def test_an_anonymous_visitor_can_add_to_the_cart(client, catalog):
    """No sign-in wall in front of the primary action; the account is only
    needed at checkout."""
    course = catalog[0]

    html = client.get(f"/course/{course.slug}").text

    assert "Add to cart" in html
    assert "Sign in to enroll" not in html


def test_the_dashboard_lists_enrolled_courses(logged_in, db, learner, catalog):
    db.add(Enrollment(user_id=learner.id, product_id=catalog[0].id))
    db.commit()

    html = logged_in.get("/dashboard").text

    assert "Your courses" in html
    assert catalog[0].title in html


# --- the reason this exists: don't sell someone what they own ----------------

def test_an_enrolled_course_is_never_recommended(db, learner, catalog, fake_mesh):
    """Recommending a course the learner already bought is the classic
    recommender failure — it reads as though nothing was being tracked."""
    owned = catalog[0]
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.add(Enrollment(user_id=learner.id, product_id=owned.id))
    db.commit()

    recommendation, _ = generate_recommendation(db, learner.id)
    db.commit()

    assert recommendation is not None
    assert owned.id not in [item.product_id for item in recommendation.items]


def test_the_owned_course_is_dropped_even_if_the_model_asks_for_it(
    db, learner, catalog, fake_mesh
):
    """Retrieval filters them out, but the model can still name one from the
    behaviour summary — finalize is the gate that actually counts."""
    from app.agent.nodes import finalize

    owned, allowed = catalog[0], catalog[1]
    state = {
        "exclude_product_ids": [owned.id],
        "candidates": [
            {"product_id": owned.id, "title": owned.title, "vector_score": 0.9},
            {"product_id": allowed.id, "title": allowed.title, "vector_score": 0.8},
        ],
        "graded": [],
        "result": {
            "headline": "h",
            "narrative": "n",
            "cta": "c",
            "items": [
                {"product_id": owned.id, "why_this": "you already own this"},
                {"product_id": allowed.id, "why_this": "this one is new to you"},
            ],
        },
    }

    out = finalize(state)

    ids = [i["product_id"] for i in out["result"]["items"]]
    assert owned.id not in ids
    assert allowed.id in ids


def test_recommendations_still_work_when_nothing_is_enrolled(db, learner, catalog, fake_mesh):
    add_events(db, learner, catalog, *AGENTIC_SESSION)
    db.commit()

    recommendation, _ = generate_recommendation(db, learner.id)
    db.commit()

    assert recommendation is not None and recommendation.items
