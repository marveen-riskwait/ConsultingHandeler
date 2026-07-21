"""Customer-facing chat: the client talks to the ORGANIZATION, in the
conversation attached to their file, which the assigned team reads. Direct
messages stay strictly staff-to-staff, and the boundary is enforced
server-side.
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


def test_portal_user_has_nobody_to_pick_from(client, portal):
    """No individual to choose: the client has one conversation, on their file."""
    directory = client.get("/api/chat/users",
                           headers=auth(portal["token"])).get_json()
    assert directory == []


def test_portal_user_cannot_dm_anyone(client, portal):
    """Server-side enforcement, not just a hidden UI — including the person who
    used to be their "reference contact"."""
    for target in ("analyst_id", "officer_id"):
        r = client.post("/api/chat/rooms", headers=auth(portal["token"]),
                        json={"user_id": portal[target]})
        assert r.status_code == 403
        assert "not allowed" in r.get_json()["message"].lower()


def test_portal_user_gets_their_customer_room_without_creating_anything(client, portal):
    rooms = client.get("/api/chat/rooms",
                       headers=auth(portal["token"])).get_json()
    assert len(rooms) == 1
    room = rooms[0]
    assert room["is_customer_room"] is True
    assert room["customer_id"] == portal["customer_id"]
    assert room["display_name"] == "Marie Dupont"


def test_portal_user_cannot_create_groups(client, portal):
    r = client.post("/api/chat/rooms", headers=auth(portal["token"]),
                    json={"name": "My own group", "member_ids": []})
    assert r.status_code == 403


def test_staff_directory_holds_colleagues_only(client, tokens, portal):
    """A client is not a chat contact — they are reached through their file."""
    to = tokens["officer@test.io"]
    directory = client.get("/api/chat/users", headers=auth(to)).get_json()
    assert all(u["is_portal_user"] is False for u in directory)
    assert portal["user_id"] not in {u["id"] for u in directory}

    # And a staff member cannot open a private thread with a client either.
    r = client.post("/api/chat/rooms", headers=auth(to),
                    json={"user_id": portal["user_id"]})
    assert r.status_code == 403


def test_staff_reach_the_client_through_the_customer_room(client, tokens, portal):
    to = tokens["officer@test.io"]
    r = client.post(f"/api/customers/{portal['customer_id']}/chat-room",
                    headers=auth(to))
    assert r.status_code == 200
    room = r.get_json()
    assert room["is_customer_room"] is True

    sent = client.post(f"/api/chat/rooms/{room['id']}/messages", headers=auth(to),
                       json={"body": "Could you send your proof of address?"})
    assert sent.status_code == 201

    # The client sees the message — attributed to the organization, not to a
    # named officer, while the author is still recorded for the audit trail.
    msgs = client.get(f"/api/chat/rooms/{room['id']}/messages",
                      headers=auth(portal["token"])).get_json()
    posted = msgs[-1]
    assert posted["from_staff"] is True
    assert posted["organization_name"] == "Test Org"
    assert posted["sender_id"] is not None


def test_directory_search_filters(client, tokens):
    to = tokens["officer@test.io"]
    all_users = client.get("/api/chat/users", headers=auth(to)).get_json()
    assert len(all_users) > 1
    filtered = client.get("/api/chat/users?q=analyst",
                          headers=auth(to)).get_json()
    assert filtered and all("analyst" in (u["full_name"] or "").lower()
                            or "analyst" in u["email"].lower()
                            for u in filtered)


def test_the_team_on_the_case_reads_the_conversation(client, tokens, portal, app):
    """The point of the whole change: access follows the team handling the file,
    not a person. Put the case on a team, and every member of that team can
    read the client's conversation."""
    from api.models import db, Case, Team, TeamMembership, User
    from api.engine import customer_chat

    with app.app_context():
        team = Team.query.first()
        analyst = User.query.filter_by(email="analyst@test.io").first()
        if not TeamMembership.query.filter_by(team_id=team.id,
                                              user_id=analyst.id).first():
            db.session.add(TeamMembership(team_id=team.id, user_id=analyst.id))
        case = Case(customer_id=portal["customer_id"], case_type="KYC_REVIEW",
                    title="Periodic review", team_id=team.id)
        db.session.add(case)
        db.session.commit()
        customer_chat.sync_for_case(case)
        db.session.commit()

    rooms = client.get("/api/chat/rooms",
                       headers=auth(tokens["analyst@test.io"])).get_json()
    assert any(r.get("is_customer_room") and
               r["customer_id"] == portal["customer_id"] for r in rooms), \
        "a member of the assigned team reads the customer conversation"


def test_an_unassigned_conversation_can_be_picked_up(client, tokens, app):
    """A client message must never fall into a void because no rule matched."""
    from api.models import db, Customer, User
    from api.auth import hash_password, make_token
    from api.rbac import get_role

    with app.app_context():
        org_id = User.query.filter_by(email="officer@test.io").first().organization_id
        customer = Customer(organization_id=org_id, name="Nobody Assigned Ltd",
                            customer_type="COMPANY", status="ONBOARDING")
        db.session.add(customer)
        db.session.flush()
        role = get_role("CUSTOMER_USER")
        pu = User(email="orphan@test.io", full_name="Orphan Portal",
                  role="CUSTOMER_USER", role_id=role.id if role else None,
                  password=hash_password("pw"), organization_id=org_id,
                  customer_id=customer.id, is_active=True)
        db.session.add(pu)
        db.session.commit()
        cid, ptoken = customer.id, make_token(pu)

    from api.engine import customer_chat
    from api.models import Customer as C
    with app.app_context():
        assert customer_chat.is_unassigned(C.query.get(cid)) is True

    # The client still has a conversation…
    rooms = client.get("/api/chat/rooms", headers=auth(ptoken)).get_json()
    assert len(rooms) == 1 and rooms[0]["customer_id"] == cid

    # …and a staff member allowed to see customers can take it.
    r = client.post(f"/api/customers/{cid}/chat-room",
                    headers=auth(tokens["officer@test.io"]))
    assert r.status_code == 200
