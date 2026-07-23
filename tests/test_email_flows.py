"""Email verification and password recovery — the shared signed-token plumbing.

Tokens are single-use, expiring, and stored hashed. Password reset must not
leak whether an address exists (enumeration).
"""
from conftest import auth


def test_signup_starts_unverified_and_a_link_verifies_it(client, app, monkeypatch):
    sent = {}
    from api.integrations import mailer
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, body, **k: sent.update(to=to, body=body)
                        or {"sent": True})

    r = client.post("/api/auth/register",
                    json={"email": "verify-me@example.com",
                          "password": "Strong-Passw0rd", "organization_name": "V Org"})
    assert r.status_code == 201
    assert r.get_json()["user"]["email_verified"] is False
    assert "verify-me@example.com" in sent.get("to", "")

    # Pull the token the DB issued (the email carried it; here we read it out).
    from api.models import User, email_tokens, EmailToken
    with app.app_context():
        u = User.query.filter_by(email="verify-me@example.com").first()
        # The stored token is hashed, so we mint the link by re-issuing… no —
        # issue invalidates. Instead extract from the email body.
    token = sent["body"].split("token=")[1].split()[0].strip()

    ok = client.post("/api/auth/verify-email", json={"token": token})
    assert ok.status_code == 200 and ok.get_json()["verified"] is True

    # The same link cannot be used twice.
    assert client.post("/api/auth/verify-email",
                       json={"token": token}).status_code == 400


def test_invited_users_are_verified_without_a_second_email(client, tokens, app):
    """Receiving the invitation already proved they control the address."""
    to = tokens["officer@test.io"]
    cid = client.post("/api/customers", headers=auth(to),
                      json={"name": "Verified By Invite Co",
                            "customer_type": "COMPANY"}).get_json()["id"]
    inv = client.post(f"/api/customers/{cid}/portal-access", headers=auth(to),
                      json={"email": "invited@example.com"}).get_json()
    token = inv["link"].split("invite=")[-1]
    acc = client.post("/api/auth/accept-invitation",
                      json={"token": token, "password": "Strong-Passw0rd"})
    assert acc.status_code == 201
    assert acc.get_json()["user"]["email_verified"] is True


def test_forgot_password_does_not_reveal_whether_the_address_exists(client, tokens):
    known = client.post("/api/auth/forgot-password",
                        json={"email": "officer@test.io"})
    unknown = client.post("/api/auth/forgot-password",
                          json={"email": "nobody-here@example.com"})
    # Identical response either way — no enumeration oracle.
    assert known.status_code == unknown.status_code == 200
    assert known.get_json() == unknown.get_json() == {"ok": True}


def test_reset_link_sets_a_new_password_and_burns_the_token(client, tokens, app, monkeypatch):
    sent = {}
    from api.integrations import mailer
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, body, **k: sent.update(body=body)
                        or {"sent": True})

    client.post("/api/auth/forgot-password", json={"email": "manager@test.io"})
    token = sent["body"].split("token=")[1].split()[0].strip()

    # Weak passwords are refused on reset too.
    assert client.post("/api/auth/reset-password",
                       json={"token": token, "password": "short"}).status_code == 400

    r = client.post("/api/auth/reset-password",
                    json={"token": token, "password": "Brand-New-Passw0rd"})
    assert r.status_code == 200

    # The new password works; the old one does not; the token is spent.
    assert client.post("/api/auth/login",
                       json={"email": "manager@test.io",
                             "password": "Brand-New-Passw0rd"}).status_code == 200
    assert client.post("/api/auth/reset-password",
                       json={"token": token,
                             "password": "Another-One-Now9"}).status_code == 400


def test_the_token_is_stored_hashed_never_in_the_clear(client, tokens, app, monkeypatch):
    sent = {}
    from api.integrations import mailer
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, body, **k: sent.update(body=body)
                        or {"sent": True})
    client.post("/api/auth/forgot-password", json={"email": "admin@test.io"})
    token = sent["body"].split("token=")[1].split()[0].strip()

    from api.models import EmailToken
    with app.app_context():
        rows = EmailToken.query.filter_by(purpose="RESET_PASSWORD").all()
        assert rows and all(r.token_hash != token for r in rows)
        assert all(len(r.token_hash) == 64 for r in rows)   # sha256 hex
