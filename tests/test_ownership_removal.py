"""Removing an erroneous owner/director: edge deactivated (history kept),
UBO set recomputed, change events emitted — a FALSE_POSITIVE case decision
never rewrites KYB data, this endpoint is how staff corrects the graph."""
from conftest import auth


def _fresh_company(client, token, name):
    return client.post("/api/customers", headers=auth(token),
                       json={"name": name, "customer_type": "COMPANY",
                             "country": "Luxembourg"}).get_json()["id"]


def test_remove_ownership_edge_recomputes_ubos(client, tokens):
    t = tokens["officer@test.io"]
    cid = _fresh_company(client, t, "Removal Test Co")

    r = client.post(f"/api/customers/{cid}/ownership", headers=auth(t),
                    json={"owner_name": "Bogus Ubo", "owner_kind": "PERSON",
                          "relationship_type": "SHAREHOLDER", "percentage": 75})
    assert r.status_code == 201
    edge_id = r.get_json()["edge"]["id"]

    g = client.get(f"/api/customers/{cid}/ownership", headers=auth(t)).get_json()
    assert [u["party"]["name"] for u in g["ubos"]] == ["Bogus Ubo"]

    r = client.delete(f"/api/customers/{cid}/ownership/{edge_id}",
                      headers=auth(t))
    assert r.status_code == 200
    body = r.get_json()
    assert body["removed"] is True
    assert "OWNERSHIP_CHANGED" in body["events"]
    assert "UBO_CHANGED" in body["events"]

    g = client.get(f"/api/customers/{cid}/ownership", headers=auth(t)).get_json()
    assert g["ubos"] == []
    assert g["graph"]["edges"] == []


def test_remove_ownership_is_tenant_scoped(client, tokens):
    t = tokens["officer@test.io"]
    cid = _fresh_company(client, t, "Removal Scope Co")
    r = client.post(f"/api/customers/{cid}/ownership", headers=auth(t),
                    json={"owner_name": "Someone", "percentage": 30})
    edge_id = r.get_json()["edge"]["id"]
    # The outsider's org cannot see the customer at all.
    r = client.delete(f"/api/customers/{cid}/ownership/{edge_id}",
                      headers=auth(tokens["outsider@test.io"]))
    assert r.status_code in (403, 404)
