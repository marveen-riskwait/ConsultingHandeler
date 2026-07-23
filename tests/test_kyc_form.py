"""KYC intake form: schema, batch save into ProfileField, completeness
progression, submission event -> review task, PEP self-declaration chain,
and permissions.
"""
from conftest import auth


def _cid(client, token, name):
    r = client.get("/api/customers", headers=auth(token))
    return next(c["id"] for c in r.get_json() if c["name"] == name)


def _fresh(client, token, name):
    """A customer of its own. The seeded ones are shared by the whole module,
    so document state from an earlier test would leak into the next."""
    return client.post("/api/customers", headers=auth(token),
                       json={"name": name, "customer_type": "INDIVIDUAL",
                             "country": "Luxembourg"}).get_json()["id"]


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


def _upload(client, token, cid, doc_type, filename="scan.pdf",
            content=b"%PDF-1.4 fake scan", mimetype="application/pdf"):
    import io
    return client.post(
        f"/api/customers/{cid}/documents", headers=auth(token),
        data={"doc_type": doc_type,
              "file": (io.BytesIO(content), filename, mimetype)},
        content_type="multipart/form-data")


def test_proof_documents_advance_requirements(client, tokens):
    t = tokens["analyst@test.io"]
    cid = _cid(client, t, "Marie Dupont")
    assert _upload(client, t, cid, "PASSPORT").status_code == 201
    assert _upload(client, t, cid, "PROOF_OF_ADDRESS",
                   filename="utility-bill.pdf").status_code == 201
    form = client.get(f"/api/customers/{cid}/kyc-form", headers=auth(t)).get_json()
    by_code = {r["code"]: r["status"] for r in form["completeness"]["requirements"]}
    assert by_code["IDENTITY_DOCUMENT"] in ("RECEIVED", "VERIFIED")
    assert by_code["PROOF_OF_ADDRESS"] in ("RECEIVED", "VERIFIED")


def test_uploaded_file_is_stored_and_readable_back(client, tokens):
    """The point of the feature: a real scan arrives, is served back, and the
    file itself is what the reviewer opens."""
    t = tokens["analyst@test.io"]
    cid = _fresh(client, t, "Scan Sender")
    r = _upload(client, t, cid, "PASSPORT", filename="passport-scan.pdf",
                content=b"%PDF-1.4 passport bytes")
    doc = r.get_json()
    assert doc["has_file"] is True
    assert doc["file_name"] == "passport-scan.pdf"
    assert doc["media_type"] == "application/pdf"
    assert doc["file_size"] == len(b"%PDF-1.4 passport bytes")

    served = client.get(doc["file_url"])
    assert served.status_code == 200
    assert b"passport bytes" in served.data


def test_a_document_without_a_file_does_not_satisfy_a_requirement(client, tokens):
    """The reported bug: "Document recorded" with nothing received used to move
    the completeness bar. Declaring an expected document is still allowed, but
    it stays MISSING until the file actually arrives."""
    t = tokens["analyst@test.io"]
    cid = _fresh(client, t, "Awaiting Documents")
    client.post(f"/api/customers/{cid}/documents", headers=auth(t),
                json={"doc_type": "PASSPORT"})
    form = client.get(f"/api/customers/{cid}/kyc-form", headers=auth(t)).get_json()
    by_code = {r["code"]: r["status"] for r in form["completeness"]["requirements"]}
    assert by_code["IDENTITY_DOCUMENT"] == "MISSING"

    _upload(client, t, cid, "PASSPORT")
    form = client.get(f"/api/customers/{cid}/kyc-form", headers=auth(t)).get_json()
    by_code = {r["code"]: r["status"] for r in form["completeness"]["requirements"]}
    assert by_code["IDENTITY_DOCUMENT"] in ("RECEIVED", "VERIFIED")


def test_document_can_be_removed_and_the_requirement_reopens(client, tokens):
    t = tokens["analyst@test.io"]
    cid = _fresh(client, t, "Sent By Mistake")
    doc = _upload(client, t, cid, "PASSPORT").get_json()

    assert client.delete(f"/api/customers/{cid}/documents/{doc['id']}",
                         headers=auth(t)).status_code == 200
    form = client.get(f"/api/customers/{cid}/kyc-form", headers=auth(t)).get_json()
    by_code = {r["code"]: r["status"] for r in form["completeness"]["requirements"]}
    assert by_code["IDENTITY_DOCUMENT"] == "MISSING"


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


def test_form_address_syncs_to_customer_addresses(client, tokens):
    """The structured address block mirrors into the Address history: one
    current RESIDENTIAL row, no duplicate on an unchanged re-save, and a
    change supersedes (history kept)."""
    t = tokens["analyst@test.io"]
    cid = _fresh(client, t, "Address Sync Person")

    addr = {"residential_street_number": "12",
            "residential_street_name": "Rue de la Paix",
            "residential_city": "Luxembourg",
            "residential_postal_code": "L-1330",
            "residential_country": "Luxembourg"}
    r = client.post(f"/api/customers/{cid}/kyc-form", headers=auth(t),
                    json={"fields": addr})
    assert r.status_code == 200

    rows = client.get(f"/api/customers/{cid}/addresses",
                      headers=auth(t)).get_json()
    assert len(rows) == 1
    a = rows[0]
    assert a["line1"] == "12 Rue de la Paix"
    assert a["postal_code"] == "L-1330"
    assert a["city"] == "Luxembourg"
    assert a["is_current"] is True

    # Unchanged re-save: no new field writes, no address churn.
    client.post(f"/api/customers/{cid}/kyc-form", headers=auth(t),
                json={"fields": addr})
    rows = client.get(f"/api/customers/{cid}/addresses",
                      headers=auth(t)).get_json()
    assert len(rows) == 1

    # Moving supersedes the old address instead of overwriting it.
    client.post(f"/api/customers/{cid}/kyc-form", headers=auth(t),
                json={"fields": {**addr, "residential_street_number": "44",
                                 "residential_street_name": "Avenue Kennedy"}})
    rows = client.get(f"/api/customers/{cid}/addresses",
                      headers=auth(t)).get_json()
    assert len(rows) == 2
    current = [a for a in rows if a["is_current"]]
    assert len(current) == 1
    assert current[0]["line1"] == "44 Avenue Kennedy"
