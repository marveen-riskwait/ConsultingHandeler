"""Dual control (maker-checker): a customer deletion under policy is held for
a second approval; the maker cannot approve their own request; a different
checker executes it; rejection leaves the customer intact."""
from conftest import auth


def _customer(client, token, name):
    return client.post("/api/customers", headers=auth(token),
                       json={"name": name, "customer_type": "INDIVIDUAL",
                             "country": "Luxembourg"}).get_json()["id"]


def _delete(client, token, cid, name):
    return client.delete(f"/api/customers/{cid}", headers=auth(token),
                         json={"confirm_name": name, "reason": "Erroneous record"})


def _enable(client, token):
    return client.put("/api/dual-control/policy", headers=auth(token),
                      json={"action_type": "CUSTOMER_DELETE", "enabled": True})


def test_disabled_by_default_deletes_immediately(client, tokens):
    officer = tokens["officer@test.io"]
    cid = _customer(client, officer, "DC Off Co")
    r = _delete(client, officer, cid, "DC Off Co")
    assert r.status_code == 200
    assert r.get_json().get("dual_control") is None


def test_enabled_holds_for_second_approval(client, tokens):
    admin = tokens["admin@test.io"]
    officer = tokens["officer@test.io"]
    assert _enable(client, admin).status_code == 200

    cid = _customer(client, officer, "DC On Co")
    r = _delete(client, officer, cid, "DC On Co")
    assert r.status_code == 202
    assert r.get_json()["dual_control"] is True
    # Customer still exists.
    assert client.get(f"/api/customers/{cid}", headers=auth(officer)).status_code == 200

    req_id = r.get_json()["request"]["id"]
    # Four-eyes: the maker (officer) cannot approve their own request.
    self_ = client.post(f"/api/dual-control/{req_id}/approve", headers=auth(officer))
    assert self_.status_code == 403

    # A different checker (manager) approves -> the deletion runs.
    ok = client.post(f"/api/dual-control/{req_id}/approve",
                     headers=auth(tokens["manager@test.io"]))
    assert ok.status_code == 200
    assert ok.get_json()["status"] == "EXECUTED"
    assert client.get(f"/api/customers/{cid}", headers=auth(officer)).status_code == 404

    # Turn the policy back off so it doesn't leak into other tests' order.
    client.put("/api/dual-control/policy", headers=auth(admin),
               json={"action_type": "CUSTOMER_DELETE", "enabled": False})


def test_reject_leaves_customer_intact(client, tokens):
    admin = tokens["admin@test.io"]
    officer = tokens["officer@test.io"]
    _enable(client, admin)
    cid = _customer(client, officer, "DC Reject Co")
    r = _delete(client, officer, cid, "DC Reject Co")
    req_id = r.get_json()["request"]["id"]
    rej = client.post(f"/api/dual-control/{req_id}/reject",
                      headers=auth(tokens["manager@test.io"]),
                      json={"reason": "Keep it — active relationship"})
    assert rej.get_json()["status"] == "REJECTED"
    assert client.get(f"/api/customers/{cid}", headers=auth(officer)).status_code == 200
    client.put("/api/dual-control/policy", headers=auth(admin),
               json={"action_type": "CUSTOMER_DELETE", "enabled": False})


def test_policy_toggle_requires_admin(client, tokens):
    # An officer cannot flip the org policy (needs organization.update).
    r = client.put("/api/dual-control/policy", headers=auth(tokens["officer@test.io"]),
                   json={"action_type": "CUSTOMER_DELETE", "enabled": True})
    assert r.status_code == 403
