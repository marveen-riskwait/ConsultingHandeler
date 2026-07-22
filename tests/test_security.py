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
