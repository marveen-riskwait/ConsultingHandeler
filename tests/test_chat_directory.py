"""Customer-facing chat: a portal user reaches ONLY their reference contacts,
staff reach colleagues and customers, and the boundary is enforced server-side.
"""
import pytest

from conftest import auth


@pytest.fixture()
def portal(app, client, tokens):
    """A customer portal user attached to 'Marie Dupont', whose relationship
    manager is the officer. Returns (token, ids…)."""
    from api.models import db, User, Customer
    from api.auth import hash_password, make_token
    from api.rbac import get_role

    with app.app_context():
        customer = Customer.query.filter_by(name="Marie Dupont").first()
        officer = User.query.filter_by(email="officer@test.io").first()
        analyst = User.query.filter_by(email="analyst@test.io").first()
        customer.relationship_manager_id = officer.id

        user = User.query.filter_by(email="client@test.io").first()
        if user is None:
            role = get_role("CUSTOMER_USER")
            user = User(email="client@test.io", full_name="Client Portal",
                        role="CUSTOMER_USER", role_id=role.id if role else None,
                        password=hash_password("pw"),
                        organization_id=customer.organization_id,
                        customer_id=customer.id, is_active=True)
            db.session.add(user)
        else:
            user.customer_id = customer.id
        db.session.commit()
        return {"token": make_token(user), "user_id": user.id,
                "officer_id": officer.id, "analyst_id": analyst.id,
                "customer_id": customer.id}


def test_portal_user_sees_only_their_reference(client, portal):
    directory = client.get("/api/chat/users",
                           headers=auth(portal["token"])).get_json()
    ids = {u["id"] for u in directory}
    assert portal["officer_id"] in ids, "the relationship manager is reachable"
    assert portal["analyst_id"] not in ids, "the rest of the team is not"


def test_portal_user_cannot_dm_anyone_else(client, portal):
    """Server-side enforcement, not just a hidden UI."""
    r = client.post("/api/chat/rooms", headers=auth(portal["token"]),
                    json={"user_id": portal["analyst_id"]})
    assert r.status_code == 403
    assert "not allowed" in r.get_json()["message"].lower()

    ok = client.post("/api/chat/rooms", headers=auth(portal["token"]),
                     json={"user_id": portal["officer_id"]})
    assert ok.status_code in (200, 201)


def test_portal_user_cannot_create_groups(client, portal):
    r = client.post("/api/chat/rooms", headers=auth(portal["token"]),
                    json={"name": "My own group", "member_ids": []})
    assert r.status_code == 403


def test_staff_can_message_a_customer(client, tokens, portal):
    """The officer sees the portal user (flagged, with its customer name)
    and can open a conversation with them."""
    to = tokens["officer@test.io"]
    directory = client.get("/api/chat/users", headers=auth(to)).get_json()
    entry = next((u for u in directory if u["id"] == portal["user_id"]), None)
    assert entry is not None and entry["is_portal_user"] is True
    assert entry["customer_name"] == "Marie Dupont"

    r = client.post("/api/chat/rooms", headers=auth(to),
                    json={"user_id": portal["user_id"]})
    assert r.status_code in (200, 201)


def test_directory_search_filters(client, tokens):
    to = tokens["officer@test.io"]
    all_users = client.get("/api/chat/users", headers=auth(to)).get_json()
    assert len(all_users) > 1
    filtered = client.get("/api/chat/users?q=analyst",
                          headers=auth(to)).get_json()
    assert filtered and all("analyst" in (u["full_name"] or "").lower()
                            or "analyst" in u["email"].lower()
                            for u in filtered)
