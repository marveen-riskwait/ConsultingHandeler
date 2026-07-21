"""Enrichment engine: field provenance, human-data protection (discrepancies),
party ingestion, adverse-media event chain, endpoint gating. Fully offline —
external sources are faked.
"""
from conftest import auth


def _cid(client, token, name):
    r = client.get("/api/customers", headers=auth(token))
    return next(c["id"] for c in r.get_json() if c["name"] == name)


class FakeRegistry:
    """Stands in for a registry source (GLEIF/Sirene/CH shape)."""
    name = "fake_registry"

    def applies(self, customer):
        return True

    def run(self, customer, context=None):
        return {"source": self.name, "ok": True, "detail": "match",
                "fields": {
                    "legal_form": {"value": "Private limited company",
                                   "confidence": 0.95},
                    "registered_office": {"value": "1 Test Street, Testville",
                                          "confidence": 0.95},
                    # Conflicts with a human-declared value in the test below.
                    "occupation": {"value": "Registry says trader",
                                   "confidence": 0.95},
                },
                "parties": [{"name": "Ulla Ubo", "kind": "PERSON",
                             "relationship_type": "SHAREHOLDER",
                             "percentage": 50.0, "country": "Sweden",
                             "nationality": "Swedish"}],
                "media": []}


class FakeMedia:
    name = "fake_media"

    def applies(self, customer):
        return True

    def run(self, customer, context=None):
        return {"source": self.name, "ok": True, "detail": "1 hit",
                "fields": {}, "parties": [],
                "media": [{"title": f"{customer.name} probed for fraud",
                           "url": "https://news.example/x", "date": "20260101",
                           "domain": "news.example", "severity": "HIGH"}]}


def test_enrich_fills_fields_parties_and_flags(client, tokens, monkeypatch, app):
    from api.engine import enrichment_service
    monkeypatch.setattr(enrichment_service, "sources_for",
                        lambda c: [FakeRegistry(), FakeMedia()])

    ta = tokens["analyst@test.io"]
    cid = _cid(client, ta, "Marie Dupont")

    # A human-declared value that the registry will contradict.
    client.post(f"/api/customers/{cid}/fields", headers=auth(ta),
                json={"field_key": "occupation", "value": "Architect",
                      "source": "manual"})

    r = client.post(f"/api/customers/{cid}/enrich", headers=auth(ta))
    assert r.status_code == 200
    report = r.get_json()
    assert report["fields_filled"] == 2          # occupation NOT overwritten
    assert report["parties_added"] == 1
    assert report["media_hits"] == 1
    assert report["discrepancies"] == [
        {"field": "occupation", "declared": "Architect",
         "found": "Registry says trader", "source": "fake_registry"}]

    d = client.get(f"/api/customers/{cid}", headers=auth(ta)).get_json()
    # Registry-sourced fields are auto-verified (trusted independent source).
    fields = {f["field_key"]: f for f in
              client.get(f"/api/customers/{cid}/fields",
                         headers=auth(ta)).get_json()}
    assert fields["legal_form"]["value"] == "Private limited company"
    assert fields["legal_form"]["source"] == "registry:fake_registry"
    assert fields["legal_form"]["verified"] is True
    assert fields["occupation"]["value"] == "Architect"   # human value kept

    # The UBO landed in the ownership graph.
    assert any(u["party"]["name"] == "Ulla Ubo" for u in d["ubos"])
    # Discrepancy -> DATA_VERIFICATION task; media -> adverse-media review task.
    types = {t["task_type"] for t in d["tasks"]}
    assert "DATA_VERIFICATION" in types
    assert "ADVERSE_MEDIA_REVIEW" in types


def test_enrich_is_idempotent_for_parties(client, tokens, monkeypatch):
    from api.engine import enrichment_service
    monkeypatch.setattr(enrichment_service, "sources_for",
                        lambda c: [FakeRegistry()])
    ta = tokens["analyst@test.io"]
    cid = _cid(client, ta, "John Smith")
    first = client.post(f"/api/customers/{cid}/enrich", headers=auth(ta)).get_json()
    again = client.post(f"/api/customers/{cid}/enrich", headers=auth(ta)).get_json()
    assert first["parties_added"] == 1
    assert again["parties_added"] == 0           # deduped by name
    assert again["fields_filled"] == 0           # same values -> no rewrite


def test_enrich_requires_kyc_edit(client, tokens):
    auditor_like = tokens["manager@test.io"]  # has kyc.edit via analyst base
    outsider = tokens["outsider@test.io"]
    ta = tokens["analyst@test.io"]
    cid = _cid(client, ta, "Marie Dupont")
    # Cross-org: not even visible.
    assert client.post(f"/api/customers/{cid}/enrich",
                       headers=auth(outsider)).status_code == 404


def test_gleif_and_sirene_parsers(monkeypatch):
    """Adapter parsing against canned API payloads."""
    from api.integrations.enrichment import gleif, sirene

    class C:  # minimal customer stub
        name = "Acme Bank"
        customer_type = "COMPANY"
        country = "France"
        id = 1

    monkeypatch.setattr(gleif, "get_json", lambda url, headers=None: {
        "data": [{"attributes": {
            "lei": "5493001KJTIIGC8Y1R12",
            "registration": {"status": "ISSUED"},
            "entity": {"legalName": {"name": "ACME BANK SA"},
                       "legalForm": {"id": "XJHM"},
                       "legalAddress": {"addressLines": ["1 Rue X"],
                                        "city": "Paris", "postalCode": "75001",
                                        "country": "FR"}}}}]})
    out = gleif.GleifSource().run(C())
    assert out["ok"] and out["fields"]["lei"]["value"] == "5493001KJTIIGC8Y1R12"
    assert "Paris" in out["fields"]["registered_office"]["value"]

    monkeypatch.setattr(sirene, "get_json", lambda url, headers=None: {
        "results": [{"nom_complet": "ACME BANK", "siren": "123456789",
                     "nature_juridique": "5599", "date_creation": "2001-01-01",
                     "activite_principale": "64.19Z",
                     "etat_administratif": "A",
                     "siege": {"adresse": "1 RUE X", "code_postal": "75001",
                               "libelle_commune": "PARIS"},
                     "dirigeants": [{"prenoms": "Jean", "nom": "Martin",
                                     "qualite": "Président",
                                     "nationalite": "Française"}]}]})
    out = sirene.FrenchRegistrySource().run(C())
    assert out["ok"] and out["fields"]["registration_number"]["value"] == "123456789"
    assert out["parties"][0]["name"] == "Jean Martin"
    assert out["parties"][0]["relationship_type"] == "DIRECTOR"
