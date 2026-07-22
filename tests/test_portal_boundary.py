"""The customer portal boundary.

Three leaks were confirmed against the running app before this existed:
a portal user could read another customer's file, could see their own risk
rating and screening flags, and could point the Copilot at any customer in the
organization. These tests are the regression wall.
"""
import io

import pytest

from conftest import auth


@pytest.fixture()
def portal(app, client, tokens):
    """A portal account attached to 'Marie Dupont', plus another customer that
    must stay invisible to it."""
    from api.models import db, User, Customer
    from api.auth import hash_password, make_token
    from api.rbac import get_role

    with app.app_context():
        mine = Customer.query.filter_by(name="Marie Dupont").first()
        other = Customer.query.filter(Customer.id != mine.id,
                                      Customer.organization_id == mine.organization_id).first()
        user = User.query.filter_by(email="portal@test.io").first()
        if user is None:
            role = get_role("CUSTOMER_USER")
            user = User(email="portal@test.io", full_name="Marie Dupont",
                        role="CUSTOMER_USER", role_id=role.id if role else None,
                        password=hash_password("pw"),
                        organization_id=mine.organization_id,
                        customer_id=mine.id, is_active=True)
            db.session.add(user)
            db.session.commit()
        return {"token": make_token(user), "mine": mine.id, "other": other.id}


def test_staff_endpoints_are_closed_to_a_portal_account(client, portal):
    """The original leak: kyc.view is needed to fill your own form, and it
    opened every customer file in the organization."""
    t = portal["token"]
    for path in (f"/api/customers/{portal['other']}/kyc-form",
                 f"/api/customers/{portal['other']}/fields",
                 f"/api/customers/{portal['mine']}/kyc-form",
                 f"/api/customers/{portal['mine']}",
                 "/api/customers",
                 "/api/workspace",
                 "/api/alerts"):
        r = client.get(path, headers=auth(t))
        assert r.status_code == 403, f"{path} answered {r.status_code}"


def test_portal_cannot_point_the_copilot_at_anyone(client, portal):
    """It could open an AI conversation scoped to any customer, and the context
    builder fed it risk score, PEP and sanctions flags."""
    r = client.post("/api/assistant/conversations", headers=auth(portal["token"]),
                    json={"customer_id": portal["other"], "title": "probe"})
    assert r.status_code == 403


def test_the_client_sees_their_own_file_without_the_assessment(client, portal):
    """The whole point: declared identity yes, the firm's analysis no —
    showing a customer they are flagged can be unlawful disclosure."""
    r = client.get("/api/portal/me", headers=auth(portal["token"]))
    assert r.status_code == 200
    body = r.get_json()
    assert body["customer"]["name"] == "Marie Dupont"

    flat = str(body).lower()
    for forbidden in ("risk_score", "risk_level", "is_pep", "has_sanctions_match",
                      "has_adverse_media", "complex_ownership", "relationship_manager"):
        assert forbidden not in flat, f"{forbidden} reached the customer"


def test_the_questionnaire_returns_answers_but_not_verdicts(client, portal):
    r = client.get("/api/portal/kyc-form", headers=auth(portal["token"]))
    assert r.status_code == 200
    body = r.get_json()
    assert body["sections"] and body["customer"]["name"] == "Marie Dupont"
    # `verified` is the firm's judgement on an answer — not the client's business.
    for value in body["values"].values():
        assert set(value) == {"value"}
    flat = str(body).lower()
    assert "risk_level" not in flat and "is_pep" not in flat


def test_the_client_sends_a_document_and_says_what_it_is(client, portal):
    t = portal["token"]
    r = client.post("/api/portal/documents", headers=auth(t),
                    data={"doc_type": "PROOF_OF_ADDRESS",
                          "description": "Electricity bill for my flat, June",
                          "file": (io.BytesIO(b"%PDF-1.4 bill"), "bill.pdf",
                                   "application/pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 201
    doc = r.get_json()
    assert doc["description"] == "Electricity bill for my flat, June"
    assert doc["state"] == "RECEIVED"

    listed = client.get("/api/portal/documents", headers=auth(t)).get_json()
    assert any(d["id"] == doc["id"] for d in listed)

    # And can withdraw it while it is still pending.
    assert client.delete(f"/api/portal/documents/{doc['id']}",
                         headers=auth(t)).status_code == 200


def test_a_portal_account_cannot_touch_another_customers_document(client, portal, tokens, app):
    """Ids are guessable; the scope must come from the session, not the URL."""
    from api.models import db, Document
    with app.app_context():
        doc = Document(customer_id=portal["other"], doc_type="PASSPORT",
                       file_url="/api/media/x.pdf", file_name="x.pdf")
        db.session.add(doc)
        db.session.commit()
        other_doc_id = doc.id

    assert client.delete(f"/api/portal/documents/{other_doc_id}",
                         headers=auth(portal["token"])).status_code == 404


def test_staff_are_not_pushed_into_the_portal(client, tokens):
    """The boundary cuts one way only: an officer keeps the full API, and has
    no business calling the customer surface."""
    to = tokens["officer@test.io"]
    assert client.get("/api/customers", headers=auth(to)).status_code == 200
    assert client.get("/api/portal/me", headers=auth(to)).status_code == 403


def test_progress_counts_what_was_asked_not_compliance(client, portal):
    r = client.get("/api/portal/me", headers=auth(portal["token"])).get_json()
    progress = r["progress"]
    assert set(progress) == {"requested", "provided", "outstanding"}
    assert progress["requested"] >= progress["provided"]
    for item in progress["outstanding"]:
        assert set(item) == {"code", "label", "kind"}
