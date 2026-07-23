"""Trusts / legal arrangements: role-based beneficial ownership (FATF R.25).

Every settlor, trustee, protector and beneficiary is a UBO regardless of
percentage; controllers of a corporate trustee surface through it; a trust is
always complex ownership; the KYC form and requirements know the type.
"""
from conftest import auth


def _trust(client, token, name):
    return client.post("/api/customers", headers=auth(token),
                       json={"name": name, "customer_type": "TRUST",
                             "country": "Jersey"}).get_json()["id"]


def _add(client, token, cid, name, rel, kind="PERSON", pct=0, owned=None):
    body = {"owner_name": name, "owner_kind": kind,
            "relationship_type": rel, "percentage": pct}
    if owned:
        body["owned_party_id"] = owned
    return client.post(f"/api/customers/{cid}/ownership", headers=auth(token),
                       json=body).get_json()


def test_trust_roles_are_ubos_without_threshold(client, tokens):
    t = tokens["officer@test.io"]
    cid = _trust(client, t, "Family Estate Trust")

    _add(client, t, cid, "Sofia Settlor", "SETTLOR")
    _add(client, t, cid, "Théo Trustee", "TRUSTEE")
    _add(client, t, cid, "Pia Protector", "PROTECTOR")
    _add(client, t, cid, "Ben Beneficiary", "BENEFICIARY")

    g = client.get(f"/api/customers/{cid}/ownership", headers=auth(t)).get_json()
    ubos = {u["party"]["name"]: u for u in g["ubos"]}
    assert set(ubos) == {"Sofia Settlor", "Théo Trustee",
                         "Pia Protector", "Ben Beneficiary"}
    for u in ubos.values():
        assert u["is_ubo"] is True          # role-based: 0% yet UBO
        assert u["effective_ownership"] == 0
    assert ubos["Sofia Settlor"]["roles"] == ["SETTLOR"]
    assert ubos["Ben Beneficiary"]["roles"] == ["BENEFICIARY"]
    # A trust is complex ownership by definition.
    assert g["complex_ownership"] is True


def test_corporate_trustee_controllers_surface(client, tokens):
    t = tokens["officer@test.io"]
    cid = _trust(client, t, "Corporate Trustee Trust")

    out = _add(client, t, cid, "Fidu Services SA", "TRUSTEE",
               kind="ORGANIZATION")
    fidu_id = out["owner"]["id"]
    # The person behind the corporate trustee (100% shareholder of it).
    _add(client, t, cid, "Olga Owner", "SHAREHOLDER", pct=100, owned=fidu_id)

    g = client.get(f"/api/customers/{cid}/ownership", headers=auth(t)).get_json()
    ubos = {u["party"]["name"]: u for u in g["ubos"]}
    assert "Olga Owner" in ubos
    assert ubos["Olga Owner"]["is_ubo"] is True   # control through the trustee


def test_trust_schema_and_requirements(client, tokens):
    t = tokens["officer@test.io"]
    schema = client.get("/api/kyc-form/schema?customer_type=TRUST",
                        headers=auth(t)).get_json()
    keys = {s["key"] for s in schema["sections"]}
    assert "trust_identity" in keys and "trust_parties" in keys
    assert "identity" not in keys and "company" not in keys
    # Shared CDD sections still apply.
    assert "purpose" in keys and "pep" in keys
    assert any(p["doc_type"] == "TRUST_DEED" for p in schema["proofs"])

    cid = _trust(client, t, "Requirements Trust")
    reqs = client.get(f"/api/customers/{cid}/requirements",
                      headers=auth(t)).get_json()
    codes = {r["code"] for r in reqs["requirements"]}
    assert {"TRUST_DEED", "TRUSTEE_ID", "GOVERNING_LAW",
            "SETTLOR_NAMES", "BENEFICIARY_NAMES"} <= codes
