"""Startup posture and credential rules (api/security.py)."""
import pytest

from conftest import auth


def test_password_policy_rejects_weak_and_accepts_strong():
    from api.security import password_problem
    assert password_problem("a") is not None
    assert password_problem("short1!") is not None            # under 12
    assert password_problem("aaaaaaaaaaaa") is not None       # one class only
    assert password_problem("password1234") is not None       # too common
    assert password_problem("demo1234") is not None           # our own demo pass
    assert password_problem("Corr3ct-Horse-Battery") is None
    assert password_problem("a-strong-one") is None           # letters + symbol


def test_register_enforces_the_policy(client):
    r = client.post("/api/auth/register",
                    json={"email": "weak@example.com", "password": "abc",
                          "organization_name": "Weak Org"})
    assert r.status_code == 400 and "12 characters" in r.get_json()["message"]

    ok = client.post("/api/auth/register",
                     json={"email": "strong@example.com",
                           "password": "Corr3ct-Horse-Battery",
                           "organization_name": "Strong Org"})
    assert ok.status_code == 201


def test_invitation_acceptance_enforces_the_policy(client, tokens, app):
    to = tokens["officer@test.io"]
    cid = client.post("/api/customers", headers=auth(to),
                      json={"name": "Policy Co", "customer_type": "COMPANY"}
                      ).get_json()["id"]
    created = client.post(f"/api/customers/{cid}/portal-access", headers=auth(to),
                          json={"email": "weakclient@example.com"}).get_json()
    token = created["link"].split("invite=")[-1]
    r = client.post("/api/auth/accept-invitation",
                    json={"token": token, "password": "123"})
    assert r.status_code == 400


def test_secret_check_refuses_default_in_production(monkeypatch):
    from api import security
    monkeypatch.delenv("FLASK_DEBUG", raising=False)   # -> production
    monkeypatch.setenv("JWT_SECRET_KEY", "change-me-in-production")
    with pytest.raises(RuntimeError):
        security.check_startup_secret()

    monkeypatch.setenv("JWT_SECRET_KEY", "short")
    with pytest.raises(RuntimeError):
        security.check_startup_secret()

    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 48)
    security.check_startup_secret()                    # a real secret is fine

    # Development never blocks, so local dev with a throwaway key still runs.
    monkeypatch.setenv("FLASK_DEBUG", "1")
    monkeypatch.setenv("JWT_SECRET_KEY", "short")
    security.check_startup_secret()


def test_cors_is_closed_by_default_in_production(monkeypatch):
    from api import security
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert security.cors_origins() == []               # not "*"
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com, https://b.io")
    assert security.cors_origins() == ["https://app.example.com", "https://b.io"]
    monkeypatch.setenv("FLASK_DEBUG", "1")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert security.cors_origins() == "*"              # dev convenience


def test_account_locks_after_repeated_failures(client, tokens, app):
    """Rate limiting throttles volume; the account lockout stops a slow drip
    against one login. Five wrong passwords lock it, and the right password is
    then refused too — indistinguishably, so the attacker learns nothing."""
    from api.security import MAX_FAILED_LOGINS
    from api.models import User

    for _ in range(MAX_FAILED_LOGINS):
        r = client.post("/api/auth/login",
                        json={"email": "officer@test.io", "password": "wrong-one!!"})
        assert r.status_code == 401

    with app.app_context():
        u = User.query.filter_by(email="officer@test.io").first()
        assert u.locked_until is not None, "account should be locked"

    # Even the correct password is refused while locked, with the same message.
    r = client.post("/api/auth/login",
                    json={"email": "officer@test.io", "password": "pw"})
    assert r.status_code == 401
    assert r.get_json()["message"] == "Invalid credentials"


def test_a_good_login_clears_the_counter_and_stamps_last_login(client, tokens, app):
    from api.models import User
    # a couple of misses, then success resets everything
    for _ in range(2):
        client.post("/api/auth/login",
                    json={"email": "analyst@test.io", "password": "nope-nope-1!"})
    r = client.post("/api/auth/login",
                    json={"email": "analyst@test.io", "password": "pw"})
    assert r.status_code == 200
    with app.app_context():
        u = User.query.filter_by(email="analyst@test.io").first()
        assert u.failed_logins == 0 and u.locked_until is None
        assert u.last_login_at is not None


def test_failed_and_successful_logins_are_audited(client, tokens):
    client.post("/api/auth/login",
                json={"email": "manager@test.io", "password": "definitely-wrong-1!"})
    client.post("/api/auth/login",
                json={"email": "manager@test.io", "password": "pw"})
    to = tokens["admin@test.io"]
    entries = client.get("/api/audit?entity_type=user", headers=auth(to)).get_json()
    rows = entries if isinstance(entries, list) else entries.get("items", [])
    actions = {e.get("action") for e in rows}
    assert "LOGIN_FAILED" in actions and "LOGIN_OK" in actions


def test_logout_revokes_the_token_it_was_called_with(client, tokens):
    """The difference between "forgotten" and "revoked": after logout the very
    same token no longer works, even though it has not expired."""
    to = tokens["officer@test.io"]
    # Works before logout.
    assert client.get("/api/auth/me", headers=auth(to)).status_code == 200

    r = client.post("/api/auth/logout", headers=auth(to))
    assert r.status_code == 200

    # The same token is now refused everywhere.
    assert client.get("/api/auth/me", headers=auth(to)).status_code == 401
    assert client.get("/api/customers", headers=auth(to)).status_code == 401


def test_logout_only_kills_that_token_not_the_account(client, tokens, app):
    """Logging out one session must not lock the account out of a fresh login."""
    from api.models import User
    from api.auth import make_token

    to = tokens["manager@test.io"]
    client.post("/api/auth/logout", headers=auth(to))
    assert client.get("/api/auth/me", headers=auth(to)).status_code == 401

    with app.app_context():
        u = User.query.filter_by(email="manager@test.io").first()
        fresh = make_token(u)
    assert client.get("/api/auth/me", headers=auth(fresh)).status_code == 200


def test_a_disabled_account_is_cut_off_immediately(client, tokens, app):
    """Disabling a user must end their live session at once — not in 12 hours."""
    from api.models import db, User
    to = tokens["analyst@test.io"]
    assert client.get("/api/auth/me", headers=auth(to)).status_code == 200

    with app.app_context():
        u = User.query.filter_by(email="analyst@test.io").first()
        u.is_active = False
        db.session.commit()

    assert client.get("/api/auth/me", headers=auth(to)).status_code == 401

    with app.app_context():   # restore for other tests in the module
        u = User.query.filter_by(email="analyst@test.io").first()
        u.is_active = True
        db.session.commit()


def test_logout_is_audited(client, tokens):
    to = tokens["officer@test.io"]
    client.post("/api/auth/logout", headers=auth(to))
    # a fresh admin token to read the trail
    admin = tokens["admin@test.io"]
    entries = client.get("/api/audit?entity_type=user", headers=auth(admin)).get_json()
    rows = entries if isinstance(entries, list) else entries.get("items", [])
    assert "LOGOUT" in {e.get("action") for e in rows}


def test_a_portal_user_can_log_out(client, tokens, app):
    from api.models import db, User, Customer
    from api.auth import hash_password, make_token
    from api.rbac import get_role
    with app.app_context():
        c = Customer.query.filter_by(name="Marie Dupont").first()
        role = get_role("CUSTOMER_USER")
        u = User(email="logout-client@test.io", full_name="Client",
                 role="CUSTOMER_USER", role_id=role.id if role else None,
                 password=hash_password("pw"), organization_id=c.organization_id,
                 customer_id=c.id, is_active=True)
        db.session.add(u); db.session.commit()
        token = make_token(u)
    assert client.post("/api/auth/logout", headers=auth(token)).status_code == 200
    assert client.get("/api/portal/me", headers=auth(token)).status_code == 401


def test_security_headers_are_present(client):
    """Talisman sets CSP, frame and sniffing protection on every response."""
    r = client.get("/api/health")
    h = r.headers
    assert "Content-Security-Policy" in h
    csp = h["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert h.get("X-Frame-Options") == "DENY"
    assert h.get("X-Content-Type-Options") == "nosniff"
    # script-src is locked to self + the one CDN we actually use.
    assert "script-src 'self' https://cdn.jsdelivr.net" in csp


def test_hsts_only_in_production(client, monkeypatch):
    """No Strict-Transport-Security in dev (http), so local dev is not forced
    onto https it does not have."""
    r = client.get("/api/health")
    # The test app runs as dev (FLASK_DEBUG unset in conftest? is_production
    # returns True when FLASK_DEBUG != '1'); assert the header is coherent with
    # whatever is_production() reports rather than hard-coding.
    from api.security import is_production
    if is_production():
        assert "Strict-Transport-Security" in r.headers
    else:
        assert "Strict-Transport-Security" not in r.headers
