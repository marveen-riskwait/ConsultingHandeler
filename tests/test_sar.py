"""SAR/STR lifecycle: draft, four-eyes approval (author cannot approve their
own report), rejection, filing, and the goAML XML export."""
from conftest import auth


def _customer(client, token, name):
    return client.post("/api/customers", headers=auth(token),
                       json={"name": name, "customer_type": "INDIVIDUAL",
                             "country": "Luxembourg"}).get_json()["id"]


def _draft(client, token, cid, **kw):
    payload = {"report_type": "STR", "reason": "Structuring pattern observed",
               "indicators": ["STRUCTURING"], "transaction_ids": []}
    payload.update(kw)
    return client.post(f"/api/customers/{cid}/sars", headers=auth(token),
                       json=payload)


def test_four_eyes_author_cannot_approve_own(client, tokens):
    officer = tokens["officer@test.io"]
    cid = _customer(client, officer, "SAR FourEyes Co")
    sar = _draft(client, officer, cid).get_json()
    client.post(f"/api/sars/{sar['id']}/submit-for-approval", headers=auth(officer))

    # The officer both drafted and holds sar.approve — four-eyes must still
    # refuse them approving their own report.
    r = client.post(f"/api/sars/{sar['id']}/approve", headers=auth(officer))
    assert r.status_code == 403
    assert "four-eyes" in r.get_json()["message"].lower()

    # A different approver (the manager) can.
    r2 = client.post(f"/api/sars/{sar['id']}/approve",
                     headers=auth(tokens["manager@test.io"]))
    assert r2.status_code == 200
    assert r2.get_json()["status"] == "APPROVED"


def test_submit_requires_a_reason(client, tokens):
    officer = tokens["officer@test.io"]
    cid = _customer(client, officer, "SAR NoReason Co")
    sar = _draft(client, officer, cid, reason="").get_json()
    r = client.post(f"/api/sars/{sar['id']}/submit-for-approval", headers=auth(officer))
    assert r.status_code == 409


def test_reject_sends_back_to_draft_then_can_resubmit(client, tokens):
    officer = tokens["officer@test.io"]
    cid = _customer(client, officer, "SAR Reject Co")
    sar = _draft(client, officer, cid).get_json()
    client.post(f"/api/sars/{sar['id']}/submit-for-approval", headers=auth(officer))
    r = client.post(f"/api/sars/{sar['id']}/reject",
                    headers=auth(tokens["manager@test.io"]),
                    json={"reason": "Add the transaction references"})
    assert r.get_json()["status"] == "REJECTED"
    # Editing a rejected report returns it to DRAFT.
    r2 = client.patch(f"/api/sars/{sar['id']}", headers=auth(officer),
                      json={"reason": "Structuring — refs TX-1, TX-2 attached"})
    assert r2.get_json()["status"] == "DRAFT"


def test_full_flow_and_goaml_export(client, tokens):
    officer = tokens["officer@test.io"]
    cid = _customer(client, officer, "SAR Export Co")
    sar = _draft(client, officer, cid).get_json()
    client.post(f"/api/sars/{sar['id']}/submit-for-approval", headers=auth(officer))
    client.post(f"/api/sars/{sar['id']}/approve", headers=auth(tokens["manager@test.io"]))
    filed = client.post(f"/api/sars/{sar['id']}/mark-submitted", headers=auth(officer))
    assert filed.get_json()["status"] == "SUBMITTED"

    x = client.get(f"/api/sars/{sar['id']}/export.xml", headers=auth(officer))
    assert x.status_code == 200
    assert x.mimetype == "application/xml"
    body = x.data.decode()
    assert "<report>" in body
    assert sar["reference"] in body
    assert "Structuring pattern observed" in body
    assert "STRUCTURING" in body


def test_analyst_cannot_approve(client, tokens):
    officer = tokens["officer@test.io"]
    cid = _customer(client, officer, "SAR Perm Co")
    sar = _draft(client, officer, cid).get_json()
    client.post(f"/api/sars/{sar['id']}/submit-for-approval", headers=auth(officer))
    # Analyst can draft (sar.create) but not approve (sar.approve).
    r = client.post(f"/api/sars/{sar['id']}/approve",
                    headers=auth(tokens["analyst@test.io"]))
    assert r.status_code == 403
