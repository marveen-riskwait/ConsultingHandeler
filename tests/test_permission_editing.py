"""Clickable permission management: role-level toggles and per-user special
authorizations — including the access-change taking effect immediately, the
ADMIN lockout guard, and tenant/permission gating.
"""
from conftest import auth


def test_role_toggle_changes_access_immediately(client, tokens, app):
    """Revoke customer.create from KYC_ANALYST -> analyst loses the ability;
    re-grant -> restored. (Restored at the end so other tests are unaffected.)"""
    admin = tokens["admin@test.io"]
    analyst = tokens["analyst@test.io"]

    roles = client.get("/api/roles", headers=auth(admin)).get_json()
    rid = next(r["id"] for r in roles if r["name"] == "KYC_ANALYST")

    r = client.post(f"/api/roles/{rid}/permissions", headers=auth(admin),
                    json={"code": "customer.create", "enabled": False})
    assert r.status_code == 200
    assert "customer.create" not in r.get_json()["permissions"]

    denied = client.post("/api/customers", headers=auth(analyst),
                         json={"name": "Blocked Co", "customer_type": "COMPANY"})
    assert denied.status_code == 403

    r = client.post(f"/api/roles/{rid}/permissions", headers=auth(admin),
                    json={"code": "customer.create", "enabled": True})
    assert "customer.create" in r.get_json()["permissions"]
    allowed = client.post("/api/customers", headers=auth(analyst),
                          json={"name": "Allowed Co", "customer_type": "COMPANY"})
    assert allowed.status_code == 201


def test_admin_lockout_guard(client, tokens):
    admin = tokens["admin@test.io"]
    roles = client.get("/api/roles", headers=auth(admin)).get_json()
    rid = next(r["id"] for r in roles if r["name"] == "ADMIN")
    r = client.post(f"/api/roles/{rid}/permissions", headers=auth(admin),
                    json={"code": "role.update", "enabled": False})
    assert r.status_code == 409
    assert "locked out" in r.get_json()["message"]


def test_user_special_authorization_grants_and_revokes(client, tokens):
    """Analyst has no audit.view; a special grant gives it to THIS analyst only."""
    admin = tokens["admin@test.io"]
    analyst = tokens["analyst@test.io"]
    officer = tokens["officer@test.io"]

    assert client.get("/api/audit", headers=auth(analyst)).status_code == 403

    users = client.get("/api/users", headers=auth(admin)).get_json()
    uid = next(u["id"] for u in users if u["email"] == "analyst@test.io")

    r = client.post(f"/api/users/{uid}/permissions", headers=auth(admin),
                    json={"code": "audit.view", "enabled": True})
    assert r.status_code == 200
    assert "audit.view" in r.get_json()["extra_permissions"]

    # The grant works immediately, and only for that user's account.
    assert client.get("/api/audit", headers=auth(analyst)).status_code == 200

    r = client.post(f"/api/users/{uid}/permissions", headers=auth(admin),
                    json={"code": "audit.view", "enabled": False})
    assert "audit.view" not in r.get_json()["extra_permissions"]
    assert client.get("/api/audit", headers=auth(analyst)).status_code == 403
    # Officer keeps their own role-based access throughout (audit.view via role).
    assert client.get("/api/audit", headers=auth(officer)).status_code == 200


def test_permission_editing_requires_role_update(client, tokens):
    """Managers (no role.update) cannot edit roles or grant authorizations."""
    manager = tokens["manager@test.io"]
    admin = tokens["admin@test.io"]
    roles = client.get("/api/roles", headers=auth(admin)).get_json()
    rid = roles[0]["id"]
    users = client.get("/api/users", headers=auth(admin)).get_json()
    uid = users[0]["id"]

    assert client.post(f"/api/roles/{rid}/permissions", headers=auth(manager),
                       json={"code": "audit.view", "enabled": True}).status_code == 403
    assert client.post(f"/api/users/{uid}/permissions", headers=auth(manager),
                       json={"code": "audit.view", "enabled": True}).status_code == 403


def test_cross_org_user_grant_rejected(client, tokens):
    """Cannot grant permissions to a user in another organization."""
    admin = tokens["admin@test.io"]
    # outsider@test.io belongs to Other Org; find its id via its own token's /auth/me
    me = client.get("/api/auth/me", headers=auth(tokens["outsider@test.io"])).get_json()
    outsider_id = me["user"]["id"]
    r = client.post(f"/api/users/{outsider_id}/permissions", headers=auth(admin),
                    json={"code": "audit.view", "enabled": True})
    assert r.status_code == 404


def test_unknown_permission_code_rejected(client, tokens):
    admin = tokens["admin@test.io"]
    users = client.get("/api/users", headers=auth(admin)).get_json()
    uid = users[0]["id"]
    r = client.post(f"/api/users/{uid}/permissions", headers=auth(admin),
                    json={"code": "not.a.permission", "enabled": True})
    assert r.status_code == 400
