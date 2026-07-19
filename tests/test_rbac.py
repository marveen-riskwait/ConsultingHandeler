from conftest import auth


def test_analyst_can_list_customers(client, tokens):
    r = client.get("/api/customers", headers=auth(tokens["analyst@test.io"]))
    assert r.status_code == 200


def test_analyst_cannot_list_roles(client, tokens):
    # role.view is an admin permission the analyst lacks.
    r = client.get("/api/roles", headers=auth(tokens["analyst@test.io"]))
    assert r.status_code == 403


def test_admin_can_list_roles(client, tokens):
    r = client.get("/api/roles", headers=auth(tokens["admin@test.io"]))
    assert r.status_code == 200


def test_admin_cannot_view_cases(client, tokens):
    # Technical admin != compliance operator: ORGANIZATION_ADMIN lacks case.view.
    r = client.get("/api/cases", headers=auth(tokens["admin@test.io"]))
    assert r.status_code == 403


def _cid(client, token, name):
    r = client.get("/api/customers", headers=auth(token))
    return next(c["id"] for c in r.get_json() if c["name"] == name)


def test_only_officer_can_confirm_match(client, tokens):
    ta = tokens["analyst@test.io"]
    jid = _cid(client, ta, "John Smith")
    client.post(f"/api/customers/{jid}/screen", headers=auth(ta))
    case = client.get("/api/cases?status=OPEN", headers=auth(ta)).get_json()[0]
    # analyst lacks screening.confirm_match
    r = client.post(f"/api/cases/{case['id']}/decision",
                    json={"decision": "CONFIRMED_MATCH", "reason": "x"},
                    headers=auth(ta))
    assert r.status_code == 403
    # officer holds it
    r = client.post(f"/api/cases/{case['id']}/decision",
                    json={"decision": "CONFIRMED_MATCH", "reason": "DOB matches"},
                    headers=auth(tokens["officer@test.io"]))
    assert r.status_code == 200
