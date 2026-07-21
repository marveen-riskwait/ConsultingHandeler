"""Customer deletion: confirmation + reason, retention guard, cascade,
audit survival, archive alternative, and permission/tenant gating.
"""
from conftest import auth


def _create(client, token, name, **kw):
    return client.post("/api/customers", headers=auth(token),
                       json={"name": name, "customer_type": "INDIVIDUAL",
                             "country": "Luxembourg", **kw}).get_json()["id"]


def test_delete_requires_reason_and_exact_name(client, tokens):
    to = tokens["officer@test.io"]          # has customer.delete
    cid = _create(client, to, "Typo Customer")

    r = client.delete(f"/api/customers/{cid}", headers=auth(to),
                      json={"confirm_name": "Typo Customer", "reason": "x"})
    assert r.status_code == 400 and "reason" in r.get_json()["message"].lower()

    r = client.delete(f"/api/customers/{cid}", headers=auth(to),
                      json={"confirm_name": "Wrong Name",
                            "reason": "created by mistake"})
    assert r.status_code == 400 and "confirmation" in r.get_json()["message"].lower()

    # Still there.
    assert client.get(f"/api/customers/{cid}", headers=auth(to)).status_code == 200


def test_delete_removes_customer_and_dependents_but_keeps_audit(client, tokens):
    to = tokens["officer@test.io"]
    cid = _create(client, to, "Duplicate Ltd", customer_type="COMPANY")
    # Give it some dependent data (documents, fields, ownership, events).
    client.post(f"/api/customers/{cid}/documents", headers=auth(to),
                json={"doc_type": "PASSPORT"})
    client.post(f"/api/customers/{cid}/fields", headers=auth(to),
                json={"field_key": "occupation", "value": "Test"})
    client.post(f"/api/customers/{cid}/ownership", headers=auth(to),
                json={"owner_name": "Some Owner", "owner_kind": "PERSON",
                      "relationship_type": "SHAREHOLDER", "percentage": 60})

    r = client.delete(f"/api/customers/{cid}", headers=auth(to),
                      json={"confirm_name": "Duplicate Ltd",
                            "reason": "duplicate created by a UI glitch"})
    assert r.status_code == 200 and r.get_json()["deleted"] is True

    assert client.get(f"/api/customers/{cid}", headers=auth(to)).status_code == 404
    assert all(c["id"] != cid for c in
               client.get("/api/customers", headers=auth(to)).get_json())

    # Dependent rows are gone…
    from api.models import Document, ProfileField, ComplianceEvent, Party
    assert Document.query.filter_by(customer_id=cid).count() == 0
    assert ProfileField.query.filter_by(customer_id=cid).count() == 0
    assert ComplianceEvent.query.filter_by(customer_id=cid).count() == 0
    assert Party.query.filter_by(customer_id=cid).count() == 0

    # …but the audit trail survives with WHO/WHAT/WHY.
    entries = client.get("/api/audit?entity_type=customer",
                         headers=auth(to)).get_json()
    deleted = [e for e in entries
               if e["action"] == "CUSTOMER_DELETED" and e["entity_id"] == cid]
    assert deleted, "the deletion must remain in the audit trail"
    assert deleted[0]["old_value"] == "Duplicate Ltd"
    assert "duplicate created" in (deleted[0]["reason"] or "")


def test_retention_guard_blocks_customers_with_history(client, tokens):
    """A confirmed sanctions match must not be erasable — archive instead."""
    to = tokens["officer@test.io"]
    cid = _create(client, to, "Sergei Ivanov Copy", country="Russia")
    client.post(f"/api/customers/{cid}/screen", headers=auth(to))
    d = client.get(f"/api/customers/{cid}", headers=auth(to)).get_json()
    match = next(m for m in d["screening_matches"] if m["match_type"] == "SANCTIONS")
    client.post(f"/api/screening/matches/{match['id']}/review", headers=auth(to),
                json={"decision": "CONFIRMED", "reason": "true positive"})

    check = client.get(f"/api/customers/{cid}/deletion-check",
                       headers=auth(to)).get_json()
    assert check["blockers"], "confirmed match should block deletion"

    r = client.delete(f"/api/customers/{cid}", headers=auth(to),
                      json={"confirm_name": "Sergei Ivanov Copy",
                            "reason": "trying to remove a real file"})
    assert r.status_code == 409
    assert "retained" in r.get_json()["message"]
    assert client.get(f"/api/customers/{cid}", headers=auth(to)).status_code == 200

    # Archive is the sanctioned alternative and keeps everything.
    r = client.post(f"/api/customers/{cid}/archive", headers=auth(to),
                    json={"reason": "off-boarded"})
    assert r.status_code == 200 and r.get_json()["status"] == "ARCHIVED"


def test_admin_can_override_retention_guard(client, tokens):
    """Only organization.update holders may force past the guard."""
    to = tokens["officer@test.io"]
    admin = tokens["admin@test.io"]
    cid = _create(client, to, "Ivanov Forced Removal", country="Russia")
    client.post(f"/api/customers/{cid}/screen", headers=auth(to))
    d = client.get(f"/api/customers/{cid}", headers=auth(to)).get_json()
    match = next(m for m in d["screening_matches"] if m["match_type"] == "SANCTIONS")
    client.post(f"/api/screening/matches/{match['id']}/review", headers=auth(to),
                json={"decision": "CONFIRMED", "reason": "tp"})

    # The officer's force flag is ignored (no organization.update).
    r = client.delete(f"/api/customers/{cid}", headers=auth(to),
                      json={"confirm_name": "Ivanov Forced Removal",
                            "reason": "please remove", "force": True})
    assert r.status_code == 409

    # ADMIN lacks customer.delete by role — grant it as a special
    # authorization, exactly how an org would handle an exceptional erasure.
    users = client.get("/api/users", headers=auth(admin)).get_json()
    uid = next(u["id"] for u in users if u["email"] == "admin@test.io")
    client.post(f"/api/users/{uid}/permissions", headers=auth(admin),
                json={"code": "customer.delete", "enabled": True})

    r = client.delete(f"/api/customers/{cid}", headers=auth(admin),
                      json={"confirm_name": "Ivanov Forced Removal",
                            "reason": "GDPR erasure request, approved by MLRO",
                            "force": True})
    assert r.status_code == 200
    assert client.get(f"/api/customers/{cid}", headers=auth(admin)).status_code == 404


def test_delete_permission_and_tenant_gating(client, tokens):
    analyst = tokens["analyst@test.io"]     # no customer.delete
    outsider = tokens["outsider@test.io"]
    to = tokens["officer@test.io"]
    cid = _create(client, to, "Protected Customer")

    assert client.delete(f"/api/customers/{cid}", headers=auth(analyst),
                         json={"confirm_name": "Protected Customer",
                               "reason": "no rights"}).status_code == 403
    # The outsider is refused at the permission layer (403), which is checked
    # before tenant scoping; either way the customer is untouchable.
    assert client.delete(f"/api/customers/{cid}", headers=auth(outsider),
                         json={"confirm_name": "Protected Customer",
                               "reason": "other org"}).status_code in (403, 404)
    assert client.get(f"/api/customers/{cid}", headers=auth(to)).status_code == 200
