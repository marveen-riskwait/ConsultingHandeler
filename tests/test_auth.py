from conftest import auth


def test_login_ok(client):
    r = client.post("/api/auth/login",
                    json={"email": "analyst@test.io", "password": "pw"})
    assert r.status_code == 200 and "token" in r.get_json()


def test_login_wrong_password(client):
    r = client.post("/api/auth/login",
                    json={"email": "analyst@test.io", "password": "nope"})
    assert r.status_code == 401


def test_me_returns_permissions(client, tokens):
    r = client.get("/api/auth/me", headers=auth(tokens["analyst@test.io"]))
    d = r.get_json()
    assert r.status_code == 200
    assert d["user"]["email"] == "analyst@test.io"
    assert "customer.view" in d["user"]["permissions"]


def test_no_token_rejected(client):
    assert client.get("/api/auth/me").status_code in (401, 422)


def test_garbage_token_rejected(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code in (401, 422)


def test_disabled_account_cannot_login(client, tokens):
    admin = auth(tokens["admin@test.io"])
    users = client.get("/api/users", headers=admin).get_json()
    uid = next(u["id"] for u in users if u["email"] == "analyst@test.io")
    client.patch(f"/api/users/{uid}", json={"is_active": False}, headers=admin)
    r = client.post("/api/auth/login",
                    json={"email": "analyst@test.io", "password": "pw"})
    assert r.status_code == 401
    client.patch(f"/api/users/{uid}", json={"is_active": True}, headers=admin)
