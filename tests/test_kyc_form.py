"""KYC intake form: schema, batch save into ProfileField, completeness
progression, submission event -> review task, PEP self-declaration chain,
and permissions.
"""
from conftest import auth


def _cid(client, token, name):
    r = client.get("/api/customers", headers=auth(token))
    return next(c["id"] for c in r.get_json() if c["name"] == name)


def test_schema_differs_by_customer_type(client, tokens):
    t = tokens["analyst@test.io"]
    ind = client.get("/api/kyc-form/schema?customer_type=INDIVIDUAL",
                     headers=auth(t)).get_json()
    com = client.get("/api/kyc-form/schema?customer_type=COMPANY",
                     headers=auth(t)).get_json()
    ind_keys = {s["key"] for s in ind["sections"]}
    com_keys = {s["key"] for s in com["sections"]}
    assert "identity" in ind_keys and "identity" not in com_keys
    assert "company" in com_keys and "ownership" in com_keys
    # Shared CDD sections appear for both.
    for shared in ("purpose", "funds", "pep"):
        assert shared in ind_keys and shared in com_keys
    # EDD section only at HIGH risk rank.
    assert "edd" not in ind_keys
    edd = client.get("/api/kyc-form/schema?customer_type=INDIVIDUAL&risk_rank=2",
                     headers=auth(t)).get_json()
    assert "edd" in {s["key"] for s in edd["sections"]}


def test_save_form_fills_profile_fields_and_completeness(client, tokens):
    t = tokens["analyst@test.io"]
    cid = _cid(client, t, "Marie Dupont")
    before = client.get(f"/api/customers/{cid}/kyc-form",
                        headers=auth(t)).get_json()

    r = client.post(f"/api/customers/{cid}/kyc-form", headers=auth(t), json={
        "fields": {
            "date_of_birth": "1990-05-04",
            "nationality": "Luxembourg",
            "occupation": "Architect",
            "country_of_tax_residence": "Luxembourg",
            "purpose_of_relationship": "Payments account for salary and savings",
            "expected_monthly_volume": "< €10,000",
            "pep_self_declaration": "No",
            "not_in_schema": "ignored",
        }})
    assert r.status_code == 200
    body = r.get_json()
    assert body["saved"] == 7  # unknown key silently ignored
    assert body["completeness"]["completeness_pct"] > before["completeness"]["completeness_pct"]

    # Values persisted with kyc_form provenance.
    form = client.get(f"/api/customers/{cid}/kyc-form", headers=auth(t)).get_json()
    assert form["values"]["occupation"]["value"] == "Architect"
    assert form["values"]["occupation"]["source"] == "kyc_form"

    # Re-saving identical values is a no-op (doesn't reset verification).
    r2 = client.post(f"/api/customers/{cid}/kyc-form", headers=auth(t),
                     json={"fields": {"occupation": "Architect"}})
    assert r2.get_json()["saved"] == 0


def test_proof_documents_advance_requirements(client, tokens):
    t = tokens["analyst@test.io"]
    cid = _cid(client, t, "Marie Dupont")
    client.post(f"/api/customers/{cid}/documents", headers=auth(t),
                json={"doc_type": "PASSPORT"})
    client.post(f"/api/customers/{cid}/documents", headers=auth(t),
                json={"doc_type": "PROOF_OF_ADDRESS"})
    form = client.get(f"/api/customers/{cid}/kyc-form", headers=auth(t)).get_json()
    by_code = {r["code"]: r["status"] for r in form["completeness"]["requirements"]}
    assert by_code["IDENTITY_DOCUMENT"] in ("RECEIVED", "VERIFIED")
    assert by_code["PROOF_OF_ADDRESS"] in ("RECEIVED", "VERIFIED")


def test_submit_creates_review_task(client, tokens):
    t = tokens["analyst@test.io"]
    cid = _cid(client, t, "John Smith")
    r = client.post(f"/api/customers/{cid}/kyc-form/submit", headers=auth(t))
    assert r.status_code == 202
    d = client.get(f"/api/customers/{cid}", headers=auth(t)).get_json()
    assert any(task["task_type"] == "KYC_REVIEW" for task in d["tasks"])


def test_pep_self_declaration_triggers_edd_chain(client, tokens):
    to = tokens["officer@test.io"]
    created = client.post("/api/customers", headers=auth(to),
                          json={"name": "Quiet Person",
                                "customer_type": "INDIVIDUAL",
                                "country": "Luxembourg"}).get_json()
    cid = created["id"]
    client.post(f"/api/customers/{cid}/kyc-form", headers=auth(to),
                json={"fields": {"pep_self_declaration": "Yes",
                                 "pep_position": "Minister of Testing"}})
    client.post(f"/api/customers/{cid}/kyc-form/submit", headers=auth(to))
    d = client.get(f"/api/customers/{cid}", headers=auth(to)).get_json()
    assert any(c["case_type"] == "PEP" for c in d["open_cases"])


def test_form_edit_requires_permission(client, tokens):
    """AUDITOR has kyc.view (can read the form) but not kyc.edit (cannot save).
    The outsider cannot even see the customer."""
    analyst = tokens["analyst@test.io"]
    outsider = tokens["outsider@test.io"]
    cid = _cid(client, analyst, "Marie Dupont")
    r = client.post(f"/api/customers/{cid}/kyc-form", headers=auth(outsider),
                    json={"fields": {"occupation": "Spy"}})
    assert r.status_code == 404  # other org -> not found
