"""Two-factor authentication: TOTP for staff, emailed OTP for customers.

Login becomes two steps — the password returns a pending ticket that opens
nothing, and only the second factor exchanges it for a session.
"""
import pyotp

from conftest import auth


def _login(client, email, password="pw"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_staff_totp_enrollment_and_two_step_login(client, tokens, app):
    to = tokens["officer@test.io"]

    # Enroll from a live session (the profile flow).
    enroll = client.post("/api/auth/mfa/enroll", headers=auth(to)).get_json()
    assert enroll["secret"] and enroll["otpauth_uri"].startswith("otpauth://")

    totp = pyotp.TOTP(enroll["secret"])
    confirm = client.post("/api/auth/mfa/confirm", headers=auth(to),
                          json={"code": totp.now()})
    assert confirm.status_code == 200
    codes = confirm.get_json()["backup_codes"]
    assert len(codes) == 8

    # Now a fresh login needs the second factor.
    r = _login(client, "officer@test.io")
    assert r.status_code == 200
    body = r.get_json()
    assert body["mfa_required"] is True and body["method"] == "TOTP"
    ticket = body["ticket"]

    # The ticket opens nothing on its own.
    assert client.get("/api/auth/me", headers=auth(ticket)).status_code == 401

    # A wrong code is refused; the right one issues the session.
    assert client.post("/api/auth/mfa", headers=auth(ticket),
                       json={"code": "000000"}).status_code == 401
    ok = client.post("/api/auth/mfa", headers=auth(ticket),
                     json={"code": totp.now()})
    assert ok.status_code == 200
    session_token = ok.get_json()["token"]
    assert client.get("/api/auth/me", headers=auth(session_token)).status_code == 200


def test_a_backup_code_works_once(client, tokens, app):
    to = tokens["manager@test.io"]
    enroll = client.post("/api/auth/mfa/enroll", headers=auth(to)).get_json()
    totp = pyotp.TOTP(enroll["secret"])
    codes = client.post("/api/auth/mfa/confirm", headers=auth(to),
                        json={"code": totp.now()}).get_json()["backup_codes"]

    ticket = _login(client, "manager@test.io").get_json()["ticket"]
    r = client.post("/api/auth/mfa", headers=auth(ticket), json={"code": codes[0]})
    assert r.status_code == 200

    # The same backup code is now spent.
    ticket2 = _login(client, "manager@test.io").get_json()["ticket"]
    assert client.post("/api/auth/mfa", headers=auth(ticket2),
                       json={"code": codes[0]}).status_code == 401


def test_portal_customer_gets_an_emailed_code(client, tokens, app, monkeypatch):
    sent = {}
    from api.integrations import mailer
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, body, **k: sent.update(body=body)
                        or {"sent": True})

    from api.models import db, User, Customer
    from api.auth import hash_password
    from api.rbac import get_role
    with app.app_context():
        c = Customer.query.filter_by(name="Marie Dupont").first()
        role = get_role("CUSTOMER_USER")
        u = User(email="otp-client@test.io", full_name="Client",
                 role="CUSTOMER_USER", role_id=role.id if role else None,
                 password=hash_password("pw"), organization_id=c.organization_id,
                 customer_id=c.id, is_active=True,
                 mfa_enabled=True, mfa_method="EMAIL_OTP")
        db.session.add(u); db.session.commit()

    r = _login(client, "otp-client@test.io")
    body = r.get_json()
    assert body["mfa_required"] is True and body["method"] == "EMAIL_OTP"
    assert "sign-in code" in sent.get("body", "")
    code = sent["body"].split("code is: ")[1].split()[0].strip()

    ok = client.post("/api/auth/mfa", headers=auth(body["ticket"]),
                     json={"code": code})
    assert ok.status_code == 200


def test_login_without_mfa_still_works_when_not_enrolled(client, tokens):
    """2FA is not forced by default (MFA_ENFORCED off), so a user who has not
    enrolled logs in in one step — the demo keeps working."""
    r = _login(client, "analyst@test.io")
    assert r.status_code == 200
    assert "token" in r.get_json() and "mfa_required" not in r.get_json()


def test_enforcement_sends_unenrolled_staff_to_setup(client, tokens, monkeypatch):
    monkeypatch.setenv("MFA_ENFORCED", "true")
    r = _login(client, "analyst@test.io")
    assert r.status_code == 200
    assert r.get_json().get("mfa_setup_required") is True


def test_the_totp_secret_is_never_serialized(client, tokens):
    to = tokens["officer@test.io"]
    client.post("/api/auth/mfa/enroll", headers=auth(to))
    me = client.get("/api/auth/me", headers=auth(to)).get_json()
    flat = str(me).lower()
    assert "mfa_secret" not in flat and "secret" not in flat
    assert me["user"]["mfa_enabled"] in (True, False)   # status is fine
