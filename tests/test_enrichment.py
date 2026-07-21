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


# --- VIES (EU VAT) and SEC EDGAR --------------------------------------------

def test_vat_parsing_rejects_things_that_are_not_vat_numbers():
    """Without a prefix whitelist, 'notavat' parses as country NO — which would
    send a nonsense lookup to VIES and read the answer as a finding."""
    from api.integrations.enrichment.vies import split_vat

    assert split_vat("LU26375245") == ("LU", "26375245")
    assert split_vat("LU 263 752-45") == ("LU", "26375245")
    assert split_vat("EL123456789") == ("EL", "123456789")   # Greece files as EL
    assert split_vat("notavat") is None
    assert split_vat("GR123456789") is None                  # GR is not a prefix
    assert split_vat("") is None
    assert split_vat(None) is None


def test_vies_without_a_vat_number_says_so_instead_of_failing(app):
    from api.integrations.enrichment.vies import ViesSource
    from api.models import Customer

    customer = Customer(name="Some Co", customer_type="COMPANY",
                        organization_id=1)
    out = ViesSource().run(customer, context={"fields": {}})
    assert out["ok"] is False
    assert "vat" in out["detail"].lower()


def test_vies_rejection_is_a_finding_not_a_failure(app, monkeypatch):
    """A VAT number the customer declared that VIES does not recognise
    contradicts the file — that is a result worth recording, not an error."""
    from api.integrations.enrichment import vies
    from api.models import Customer

    monkeypatch.setattr(vies, "get_json", lambda url, headers=None: {"isValid": False})
    customer = Customer(name="Ghost SARL", customer_type="COMPANY",
                        organization_id=1)
    out = vies.ViesSource().run(customer, context={"fields": {"vat_number": "LU12345678"}})
    assert out["ok"] is True
    assert out["fields"]["vat_valid"]["value"] == "No"
    assert "NOT registered" in out["detail"]


def test_vies_valid_number_fills_legal_name_and_address(app, monkeypatch):
    from api.integrations.enrichment import vies
    from api.models import Customer

    monkeypatch.setattr(vies, "get_json", lambda url, headers=None: {
        "isValid": True, "name": "AMAZON EUROPE CORE S.A R.L.",
        "address": "38, AVENUE JOHN F. KENNEDY\nL-1855  LUXEMBOURG"})
    customer = Customer(name="Amazon Europe Core", customer_type="COMPANY",
                        organization_id=1)
    out = vies.ViesSource().run(customer,
                                context={"fields": {"vat_number": "LU26375245"}})
    assert out["fields"]["vat_valid"]["value"] == "Yes"
    assert out["fields"]["legal_name"]["value"] == "AMAZON EUROPE CORE S.A R.L."
    assert "JOHN F. KENNEDY" in out["fields"]["registered_office"]["value"]


def test_sec_edgar_maps_a_filer_to_registration_details(app, monkeypatch):
    from api.integrations.enrichment import sec_edgar
    from api.models import Customer

    def fake_get(url, headers=None):
        if "company_tickers" in url:
            return {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        return {"name": "Apple Inc.", "sicDescription": "Electronic Computers",
                "stateOfIncorporation": "CA",
                "addresses": {"business": {"street1": "ONE APPLE PARK WAY",
                                           "city": "CUPERTINO",
                                           "stateOrCountry": "CA",
                                           "zipCode": "95014"}}}
    monkeypatch.setattr(sec_edgar, "get_json", fake_get)

    customer = Customer(name="Apple Inc.", customer_type="COMPANY",
                        organization_id=1)
    out = sec_edgar.SecEdgarSource().run(customer)
    assert out["ok"] is True
    assert out["fields"]["sec_cik"]["value"] == "320193"
    assert out["fields"]["country_of_incorporation"]["value"] == "US-CA"
    assert "CUPERTINO" in out["fields"]["registered_office"]["value"]


def test_sec_edgar_reports_a_miss_rather_than_guessing(app, monkeypatch):
    from api.integrations.enrichment import sec_edgar
    from api.models import Customer

    monkeypatch.setattr(sec_edgar, "get_json",
                        lambda url, headers=None: {"0": {"cik_str": 1,
                                                         "title": "Zzz Corp"}})
    customer = Customer(name="Bakery Of Luxembourg", customer_type="COMPANY",
                        organization_id=1)
    out = sec_edgar.SecEdgarSource().run(customer)
    assert out["ok"] is False and "No SEC filer" in out["detail"]


def test_new_sources_are_registered_for_companies(app):
    from api.integrations.enrichment import sources_for
    from api.models import Customer

    company = Customer(name="Any Co", customer_type="COMPANY", organization_id=1)
    names = {s.name for s in sources_for(company)}
    assert {"vies", "sec_edgar"} <= names

    person = Customer(name="Marie Dupont", customer_type="INDIVIDUAL",
                      organization_id=1)
    person_names = {s.name for s in sources_for(person)}
    assert "vies" not in person_names and "sec_edgar" not in person_names


# --- Wikidata PEP lead -------------------------------------------------------

def test_wikidata_pep_is_a_lead_and_never_a_determination(app, monkeypatch):
    """It must not set is_pep or score risk on its own: the confidence stays
    below the auto-verify threshold so a human always confirms."""
    from api.integrations.enrichment import wikidata_pep as wp
    from api.models import Customer

    monkeypatch.setattr(wp, "_search", lambda n: {"id": "Q123", "label": "A Politician"})
    monkeypatch.setattr(wp, "_claims", lambda q: {"P39": [_claim("Q191954")],
                                                  "P102": [_claim("Q7278")]})
    monkeypatch.setattr(wp, "_labels", lambda ids: {"Q191954": "President of France",
                                                    "Q7278": "A Party"})
    customer = Customer(name="A Politician", customer_type="INDIVIDUAL",
                        organization_id=1)
    out = wp.WikidataPepSource().run(customer)

    assert out["ok"] is True
    assert "Possible PEP" in out["detail"] and "Confirm" in out["detail"]
    assert "President of France" in out["fields"]["pep_signal"]["value"]
    # Below the 0.9 trusted-source threshold, so it is never auto-verified.
    assert out["fields"]["pep_signal"]["confidence"] < 0.9


def _claim(qid):
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}


def test_no_wikidata_entry_is_not_a_clearance(app, monkeypatch):
    """Silence means Wikidata does not know — coverage is skewed to famous
    people. Reporting it as "not a PEP" would be dangerous."""
    from api.integrations.enrichment import wikidata_pep as wp
    from api.models import Customer

    monkeypatch.setattr(wp, "_search", lambda n: None)
    out = wp.WikidataPepSource().run(
        Customer(name="Jean Discret", customer_type="INDIVIDUAL", organization_id=1))
    assert out["ok"] is False
    assert "says nothing about" in out["detail"]
    assert out["fields"] == {}

    monkeypatch.setattr(wp, "_search", lambda n: {"id": "Q9", "label": "A Baker"})
    monkeypatch.setattr(wp, "_claims", lambda q: {})
    out2 = wp.WikidataPepSource().run(
        Customer(name="A Baker", customer_type="INDIVIDUAL", organization_id=1))
    assert out2["ok"] is False and "Not a clearance" in out2["detail"]


def test_pep_lead_applies_to_individuals_only(app):
    from api.integrations.enrichment import sources_for
    from api.models import Customer

    person = Customer(name="X", customer_type="INDIVIDUAL", organization_id=1)
    company = Customer(name="Y", customer_type="COMPANY", organization_id=1)
    assert "wikidata_pep" in {s.name for s in sources_for(person)}
    assert "wikidata_pep" not in {s.name for s in sources_for(company)}
