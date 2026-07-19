from conftest import auth


def test_analyst_sees_only_own_org(client, tokens):
    r = client.get("/api/customers", headers=auth(tokens["analyst@test.io"]))
    names = {c["name"] for c in r.get_json()}
    assert "Marie Dupont" in names
    assert "Foreign Co" not in names          # belongs to the other org


def test_cannot_access_other_org_customer(client, tokens):
    # 'Foreign Co' lives in the other org — fetch its id as the outsider…
    r = client.get("/api/customers", headers=auth(tokens["outsider@test.io"]))
    fid = next(c["id"] for c in r.get_json() if c["name"] == "Foreign Co")
    # …then a Test-Org analyst must NOT be able to read it.
    r = client.get(f"/api/customers/{fid}",
                   headers=auth(tokens["analyst@test.io"]))
    assert r.status_code == 404


def test_audit_is_org_scoped(client, tokens):
    r = client.get("/api/audit", headers=auth(tokens["officer@test.io"]))
    assert r.status_code == 200
    org_id = client.get("/api/auth/me", headers=auth(tokens["officer@test.io"])) \
        .get_json()["organization"]["id"]
    for entry in r.get_json():
        assert entry["organization_id"] in (None, org_id)
