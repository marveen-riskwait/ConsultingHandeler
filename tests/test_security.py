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
