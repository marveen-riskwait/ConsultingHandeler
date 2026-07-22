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


def test_the_portal_assistant_is_never_handed_the_assessment(app):
    """The staff context builder emits risk, PEP and sanctions. The portal one
    must be structurally incapable of it — not merely trimmed."""
    from api.engine.assistant_service import (customer_context,
                                              portal_customer_context)
    from api.models import Customer

    with app.app_context():
        customer = Customer.query.filter_by(name="Marie Dupont").first()
        customer.risk_level = "CRITICAL"
        customer.risk_score = 95
        customer.is_pep = True
        customer.has_sanctions_match = True

        staff = customer_context(customer)
        assert "CRITICAL" in staff and "Risk" in staff      # the staff view does

        client_view = portal_customer_context(customer).lower()
        # No rating, no screening outcome, no assessment vocabulary.
        for forbidden in ("critical", "risk", "sanction", "adverse media",
                          "screening", "score", "95"):
            assert forbidden not in client_view, f"{forbidden} reached the client"
        # "PEP self-declaration" may appear: that is a question the customer
        # answers about themselves, not a result of screening them. The line is
        # between what they declare and what the firm concludes.
        if "pep" in client_view:
            assert "pep self-declaration" in client_view
        assert "marie dupont" in client_view
        assert "outstanding" in client_view or "nothing is outstanding" in client_view


def test_portal_assistant_thread_is_scoped_to_the_signed_in_customer(client, portal, app):
    r = client.get("/api/portal/assistant", headers=auth(portal["token"]))
    assert r.status_code == 200
    body = r.get_json()
    assert body["suggested"] and isinstance(body["messages"], list)

    sent = client.post("/api/portal/assistant", headers=auth(portal["token"]),
                       json={"message": "What do you still need from me?"})
    assert sent.status_code == 201

    from api.models import Conversation
    with app.app_context():
        convo = Conversation.query.filter_by(customer_id=portal["mine"]).first()
        assert convo is not None and convo.customer_id == portal["mine"]

    # And the staff assistant stays closed to them.
    assert client.get("/api/assistant/meta",
                      headers=auth(portal["token"])).status_code == 403


def test_returning_a_document_reaches_the_customer_with_a_neutral_reason(client, portal, tokens, app):
    """The loop that makes the portal useful: the client sends, the analyst
    returns it with a reason, the client sees what to do — and the reason
    describes the document, never the analysis."""
    t = portal["token"]
    doc = client.post("/api/portal/documents", headers=auth(t),
                      data={"doc_type": "IDENTITY_DOCUMENT",
                            "description": "My passport",
                            "file": (io.BytesIO(b"%PDF blurry"), "passport.pdf",
                                     "application/pdf")},
                      content_type="multipart/form-data").get_json()
    assert doc["state"] == "RECEIVED"

    officer = tokens["officer@test.io"]
    r = client.post(
        f"/api/customers/{portal['mine']}/documents/{doc['id']}/review",
        headers=auth(officer), json={"decision": "RETURN",
                                     "reason_code": "UNREADABLE"})
    assert r.status_code == 200

    seen = client.get("/api/portal/documents", headers=auth(t)).get_json()
    returned = next(d for d in seen if d["id"] == doc["id"])
    assert returned["state"] == "RETURNED"
    assert "readable" in returned["returned_reason"].lower()

    # Accepting clears the reason and shows as accepted.
    client.post(f"/api/customers/{portal['mine']}/documents/{doc['id']}/review",
                headers=auth(officer), json={"decision": "ACCEPT"})
    seen = client.get("/api/portal/documents", headers=auth(t)).get_json()
    assert next(d for d in seen if d["id"] == doc["id"])["state"] == "ACCEPTED"


def test_returning_a_document_requires_a_reason(client, portal, tokens):
    t, officer = portal["token"], tokens["officer@test.io"]
    doc = client.post("/api/portal/documents", headers=auth(t),
                      data={"doc_type": "OTHER",
                            "file": (io.BytesIO(b"x"), "a.pdf", "application/pdf")},
                      content_type="multipart/form-data").get_json()
    r = client.post(f"/api/customers/{portal['mine']}/documents/{doc['id']}/review",
                    headers=auth(officer), json={"decision": "RETURN"})
    assert r.status_code == 400


# --- submitting, taking it back, and being told --------------------------------

def test_submit_then_reopen_while_nobody_has_started(client, portal, app):
    t = portal["token"]
    assert client.get("/api/portal/me", headers=auth(t)).get_json()["customer"]["submitted"] is False

    r = client.post("/api/portal/kyc-form/submit", headers=auth(t))
    assert r.status_code == 200 and r.get_json()["submitted"] is True
    assert client.post("/api/portal/kyc-form/submit",
                       headers=auth(t)).status_code == 409   # already submitted

    r = client.post("/api/portal/kyc-form/reopen", headers=auth(t))
    assert r.status_code == 200 and r.get_json()["submitted"] is False
    # And they can carry on editing.
    assert client.post("/api/portal/kyc-form", headers=auth(t),
                       json={"fields": {"occupation": "Architect"}}).status_code == 200


def test_reopen_is_refused_once_review_has_started(client, portal, app):
    """Pulling the file out from under an analyst mid-review would be worse
    than making the customer ask — so the refusal points them at the team."""
    from api.models import db, Task

    t = portal["token"]
    client.post("/api/portal/kyc-form/submit", headers=auth(t))
    with app.app_context():
        db.session.add(Task(customer_id=portal["mine"], task_type="KYC_REVIEW",
                            title="Review", status="IN_PROGRESS"))
        db.session.commit()

    r = client.post("/api/portal/kyc-form/reopen", headers=auth(t))
    assert r.status_code == 409
    assert "send us a message" in r.get_json()["message"].lower()


def test_the_email_says_nothing_about_the_file(app, monkeypatch):
    """Email is unencrypted and lands in shared inboxes. A notification may say
    that something is waiting; it may not say what was returned or why."""
    from api.integrations import mailer
    from api.models import User

    sent = {}
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, body: sent.update(
                            to=to, subject=subject, body=body) or {"sent": True})
    monkeypatch.setenv("PORTAL_URL", "https://portal.example.com")

    with app.app_context():
        user = User.query.filter_by(email="portal@test.io").first()
        mailer.notify_action_needed(user, "Acme Compliance", what="a document")

    blob = (sent["subject"] + " " + sent["body"]).lower()
    assert "acme compliance" in blob and "a document" in blob
    for forbidden in ("risk", "sanction", "pep", "unreadable", "expired",
                      "screening", "review", "passport", "proof of address"):
        assert forbidden not in blob, f"{forbidden} travelled by email"


def test_a_missing_mail_server_never_breaks_the_action(app, monkeypatch):
    """Returning a document is a compliance action; the courtesy note is not."""
    from api.integrations import mailer
    for key in ("SMTP_HOST", "BREVO_API_KEY", "MAIL_SUPPRESS", "MAIL_FROM"):
        monkeypatch.delenv(key, raising=False)
    out = mailer.send("someone@example.com", "hi", "body")
    assert out["sent"] is False
    # And the reason says what to set, rather than just failing quietly.
    assert "BREVO_API_KEY" in out["reason"] and "SMTP_HOST" in out["reason"]


def test_returning_a_document_notifies_the_customer(client, portal, tokens, app, monkeypatch):
    calls = []
    from api.integrations import mailer
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, body: calls.append(to) or {"sent": True})

    t, officer = portal["token"], tokens["officer@test.io"]
    doc = client.post("/api/portal/documents", headers=auth(t),
                      data={"doc_type": "OTHER",
                            "file": (io.BytesIO(b"x"), "a.pdf", "application/pdf")},
                      content_type="multipart/form-data").get_json()
    client.post(f"/api/customers/{portal['mine']}/documents/{doc['id']}/review",
                headers=auth(officer),
                json={"decision": "RETURN", "reason_code": "UNREADABLE"})
    assert "portal@test.io" in calls

    # Accepting is not a chore for the customer, so it does not email them.
    calls.clear()
    client.post(f"/api/customers/{portal['mine']}/documents/{doc['id']}/review",
                headers=auth(officer), json={"decision": "ACCEPT"})
    assert calls == []


def test_brevo_is_preferred_over_smtp_and_shaped_correctly(app, monkeypatch):
    """PaaS hosts block outbound 587, so an API transport that works in
    production must win over an SMTP relay that only works on a laptop."""
    from api.integrations import mailer

    monkeypatch.setenv("MAIL_FROM", "Acme Compliance <no-reply@acme.io>")
    monkeypatch.setenv("SMTP_HOST", "smtp-relay.brevo.com")
    monkeypatch.delenv("MAIL_SUPPRESS", raising=False)
    assert mailer.transport() == "smtp"

    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test")
    assert mailer.transport() == "brevo"

    captured = {}
    def fake_post(url, payload, headers=None):
        captured.update(url=url, payload=payload, headers=headers)
        return {"messageId": "<abc@brevo>"}
    monkeypatch.setattr("api.integrations.ai.base.post_json", fake_post)

    out = mailer.send("client@example.com", "Subject", "Body")
    assert out == {"sent": True, "transport": "brevo", "id": "<abc@brevo>"}
    assert captured["url"] == mailer.BREVO_ENDPOINT
    assert captured["headers"]["api-key"] == "xkeysib-test"
    # Brevo wants the sender split into name + email, not an RFC 5322 string.
    assert captured["payload"]["sender"] == {"email": "no-reply@acme.io",
                                             "name": "Acme Compliance"}
    assert captured["payload"]["to"] == [{"email": "client@example.com"}]


def test_a_broken_brevo_key_does_not_break_the_action(app, monkeypatch):
    from api.integrations import mailer

    monkeypatch.setenv("MAIL_FROM", "no-reply@acme.io")
    monkeypatch.setenv("BREVO_API_KEY", "wrong")
    monkeypatch.delenv("MAIL_SUPPRESS", raising=False)
    monkeypatch.setattr("api.integrations.ai.base.post_json",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("HTTP 401: unauthorised")))

    out = mailer.send("client@example.com", "Subject", "Body")
    assert out["sent"] is False
    assert "401" in out["reason"] and out["transport"] == "brevo"
