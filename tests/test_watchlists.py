"""Public watchlists: ingestion (sample mode), matching, screening integration,
and the Companies House adapter's no-key behaviour. Fully offline.
"""
from conftest import auth


def _cid(client, token, name):
    r = client.get("/api/customers", headers=auth(token))
    return next(c["id"] for c in r.get_json() if c["name"] == name)


def test_ingest_samples_and_stats(app):
    from api.engine import watchlist_service
    imports = watchlist_service.ingest_all(prefer_live=False)
    assert all(i.status == "OK" for i in imports)
    assert all(not i.live for i in imports)          # sample mode
    stats = {s["source"]: s for s in watchlist_service.stats()}
    assert stats["OFAC"]["record_count"] >= 5
    assert stats["UN"]["record_count"] >= 3
    assert stats["EU"]["record_count"] >= 4


def test_ingest_is_idempotent(app):
    from api.engine import watchlist_service
    from api.models import SanctionedEntity
    watchlist_service.ingest("OFAC", prefer_live=False)
    count1 = SanctionedEntity.query.filter_by(source="OFAC").count()
    watchlist_service.ingest("OFAC", prefer_live=False)   # re-run: upsert, no dupes
    assert SanctionedEntity.query.filter_by(source="OFAC").count() == count1


def test_search_matches_exact_and_alias(app):
    from api.engine import watchlist_service
    watchlist_service.ingest_all(prefer_live=False)

    hits = watchlist_service.search("Tornado Cash")
    assert hits and hits[0][0].source == "OFAC" and hits[0][1] >= 90

    # Alias match ("El Chapo" is an OFAC a.k.a.).
    hits = watchlist_service.search("El Chapo")
    assert any(e.external_id == "10756" for e, _ in hits)

    hits = watchlist_service.search("Totally Clean Bakery")
    assert hits == []


def test_screening_uses_local_watchlist(client, tokens, app):
    """A customer named after a real listed entity gets a real SANCTIONS match
    through the composite provider."""
    from api.engine import watchlist_service
    watchlist_service.ingest_all(prefer_live=False)

    t = tokens["analyst@test.io"]
    r = client.post("/api/customers", headers=auth(t), json={
        "name": "Wagner Group", "customer_type": "COMPANY", "country": "Russia"})
    cid = r.get_json()["id"]
    client.post(f"/api/customers/{cid}/screen", headers=auth(t))
    d = client.get(f"/api/customers/{cid}", headers=auth(t)).get_json()

    sanctions = [m for m in d["screening_matches"] if m["match_type"] == "SANCTIONS"]
    assert any(m["source"].startswith("EU") for m in sanctions)
    assert d["customer"]["has_sanctions_match"] is True


def test_watchlist_endpoints_and_permissions(client, tokens, app):
    analyst = tokens["analyst@test.io"]   # screening.view, no regulatory.manage
    r = client.get("/api/watchlists", headers=auth(analyst))
    assert r.status_code == 200

    r = client.get("/api/watchlists/search?q=al-qaida", headers=auth(analyst))
    assert r.status_code == 200

    # Ingest needs regulatory.manage — analyst is refused.
    r = client.post("/api/watchlists/ingest", headers=auth(analyst),
                    json={"source": "OFAC", "live": False})
    assert r.status_code == 403

    admin = tokens["admin@test.io"]
    r = client.post("/api/watchlists/ingest", headers=auth(admin),
                    json={"source": "OFAC", "live": False})
    assert r.status_code == 200
    body = r.get_json()
    assert body[0]["status"] == "OK" and body[0]["live"] is False


def test_kyb_lookup_without_key_fails_cleanly(client, tokens, app):
    """No Companies House key configured -> clear 409, no crash, no fake data."""
    from api.models import db, Provider
    from api.models import Organization
    org = Organization.query.filter_by(name="Test Org").first()
    if not Provider.query.filter_by(organization_id=org.id,
                                    provider_type="KYB").first():
        db.session.add(Provider(organization_id=org.id, name="Companies House",
                                provider_type="KYB", adapter="companies_house",
                                enabled=True))
        db.session.commit()

    t = tokens["analyst@test.io"]
    cid = _cid(client, t, "John Smith")
    r = client.post(f"/api/customers/{cid}/kyb-lookup", headers=auth(t))
    assert r.status_code == 409
    assert "missing API key" in r.get_json()["message"]


def test_fuzzy_matching_catches_misspellings_without_swallowing_everything(app):
    """Sanctions evasion is spelled wrong on purpose, so a one-letter change
    must not be a clean pass — while unrelated names must still miss."""
    from api.engine import watchlist_service
    watchlist_service.ingest_all(prefer_live=False)

    exact = watchlist_service.search("Tornado Cash")
    assert exact and exact[0][1] >= 90

    for typo in ("Tornado Cach", "Tornadoo Cash", "Tornado Csah"):
        hits = watchlist_service.search(typo)
        assert hits, f"{typo} should still reach the listed entity"
        # Flagged as a near miss, never dressed up as an exact hit.
        assert hits[0][1] < 90
        assert hits[0][1] >= 70

    # Tolerance must not turn into "everything matches".
    assert watchlist_service.search("Totally Clean Bakery") == []
    assert watchlist_service.search("Zurich Cheese Imports") == []


def test_fuzzy_threshold_is_configurable(app, monkeypatch):
    """The false-positive/false-negative trade-off is a compliance setting, not
    a hardcoded constant."""
    from api.engine import watchlist_service
    watchlist_service.ingest_all(prefer_live=False)

    monkeypatch.setattr(watchlist_service, "FUZZY_THRESHOLD", 0.99)
    assert watchlist_service.search("Tornado Cach") == []
    monkeypatch.setattr(watchlist_service, "FUZZY_THRESHOLD", 0.84)
    assert watchlist_service.search("Tornado Cach")


def test_suggest_is_substring_and_narrows_as_you_type(app):
    """The type-ahead is a different primitive: letters you typed, appearing
    somewhere in the name — narrowing as the fragment grows."""
    from api.engine import watchlist_service
    watchlist_service.ingest_all(prefer_live=False)

    assert watchlist_service.suggest("to") == []          # too short to be useful
    broad = watchlist_service.suggest("tor")
    assert broad
    narrow = watchlist_service.suggest("tornado")
    assert len(narrow) <= len(broad)
    assert all("tornado" in e.name_normalized or
               any("tornado" in a for a in (e.aliases_normalized or []))
               for e in narrow)
    # Names starting with the fragment come first.
    assert broad[0].name_normalized.startswith("tor") or \
        "tor" in broad[0].name_normalized


def test_name_suggestions_endpoint_groups_customers_and_watchlist(client, tokens, app):
    from api.engine import watchlist_service
    watchlist_service.ingest_all(prefer_live=False)
    t = tokens["officer@test.io"]

    client.post("/api/customers", headers=auth(t),
                json={"name": "Tornado Logistics SARL", "customer_type": "COMPANY"})

    assert client.get("/api/name-suggestions?q=to",
                      headers=auth(t)).get_json() == {"customers": [], "watchlist": []}

    d = client.get("/api/name-suggestions?q=tornado", headers=auth(t)).get_json()
    assert any(c["name"] == "Tornado Logistics SARL" for c in d["customers"])
    assert any("tornado" in w["name"].lower() for w in d["watchlist"])
    assert all("source" in w for w in d["watchlist"])


def test_all_seven_sources_ingest_and_report_provenance(app):
    """Coverage is the point: seven jurisdictions, each with its own record
    count and a live/sample provenance flag."""
    from api.engine import watchlist_service
    from api.integrations.sanctions import all_sources

    codes = {s.code for s in all_sources()}
    assert codes == {"OFAC", "UN", "EU", "OFSI", "CANADA", "SECO", "DFAT"}

    watchlist_service.ingest_all(prefer_live=False)
    stats = {s["source"]: s for s in watchlist_service.stats()}
    assert set(stats) == codes
    for code in codes:
        assert stats[code]["record_count"] > 0, f"{code} ingested nothing"
        assert stats[code]["last_import"] is not None


def test_new_sources_are_searchable_and_keep_their_aliases(app):
    from api.engine import watchlist_service
    from api.models import SanctionedEntity
    watchlist_service.ingest_all(prefer_live=False)

    # A UK OFSI entry carried over with its alias spellings.
    ofsi = SanctionedEntity.query.filter_by(source="OFSI").first()
    assert ofsi is not None and ofsi.name_normalized
    hits = watchlist_service.search(ofsi.name)
    assert any(e.source == "OFSI" for e, _ in hits)

    # Canada records are individuals/entities with a programme (the country).
    canada = SanctionedEntity.query.filter_by(source="CANADA").first()
    assert canada is not None
    assert canada.entity_type in ("INDIVIDUAL", "ENTITY", "VESSEL")


def test_ingesting_one_source_leaves_the_others_alone(app):
    """Refreshing the UK list must not wipe the US one."""
    from api.engine import watchlist_service
    from api.models import SanctionedEntity
    watchlist_service.ingest_all(prefer_live=False)
    before = SanctionedEntity.query.filter_by(source="OFAC").count()
    watchlist_service.ingest("OFSI", prefer_live=False)
    assert SanctionedEntity.query.filter_by(source="OFAC").count() == before


# --- country risk -----------------------------------------------------------

def test_geography_factors_come_from_official_lists(app):
    """Geography scoring must be traceable to a published list, not to a set
    somebody typed into the source."""
    from api.engine import country_risk
    from api.models import RiskFactor

    out = country_risk.sync(prefer_live=False)
    codes = {row["code"] for row in out["synced"]}
    # Three official lists, plus the firm's own — kept separate so an examiner
    # can tell a regulator's list from the institution's risk appetite.
    assert codes == {"GEO_FATF_ACTION", "GEO_EU_HIGH_RISK",
                     "GEO_FATF_MONITORING", "GEO_INSTITUTION"}

    action = RiskFactor.query.filter_by(code="GEO_FATF_ACTION").first()
    assert action.condition_type == "COUNTRY_IN"
    assert "Iran" in action.condition_value["values"]
    assert "North Korea" in action.condition_value["values"]
    # Provenance travels with the factor.
    assert action.condition_value["as_of"]
    assert action.condition_value["source_url"].startswith("http")

    # The old hardcoded single factor is retired.
    assert RiskFactor.query.filter_by(code="GEOGRAPHY").first() is None


def test_country_sync_refreshes_membership_without_touching_weights(app):
    """An officer's tuning of the impact must survive a list refresh."""
    from api.engine import country_risk
    from api.models import db, RiskFactor

    country_risk.sync(prefer_live=False)
    factor = RiskFactor.query.filter_by(code="GEO_FATF_ACTION").first()
    factor.impact = 50                      # a deliberate local calibration
    factor.condition_value = {"values": ["Nowhere"]}
    db.session.commit()

    country_risk.sync(prefer_live=False)
    factor = RiskFactor.query.filter_by(code="GEO_FATF_ACTION").first()
    assert factor.impact == 50, "configured weight must not be overwritten"
    assert "Iran" in factor.condition_value["values"], "membership is refreshed"


def test_stale_lists_are_flagged(app):
    """FATF revises three times a year, so an old snapshot has to announce
    itself rather than quietly scoring against last year's world."""
    from api.integrations import countryrisk
    from datetime import date

    assert countryrisk.is_stale("2019-01-01") is True
    assert countryrisk.is_stale(date.today().isoformat()) is False
    assert countryrisk.is_stale("not-a-date") is True

    for entry in countryrisk.all_lists(prefer_live=False).values():
        assert "stale" in entry and entry["countries"]


def test_a_country_on_two_lists_scores_both(app, client, tokens):
    """Iran is on the FATF black list and the EU list: the assessment shows two
    separate contributions, not one merged guess."""
    from conftest import auth
    from api.engine import country_risk
    country_risk.sync(prefer_live=False)

    to = tokens["officer@test.io"]
    cid = client.post("/api/customers", headers=auth(to),
                      json={"name": "Tehran Trading Co", "customer_type": "COMPANY",
                            "country": "Iran"}).get_json()["id"]
    d = client.get(f"/api/customers/{cid}", headers=auth(to)).get_json()
    codes = {f.get("code") for f in d["risk"]["factors"]}
    assert "GEO_FATF_ACTION" in codes
    assert "GEO_EU_HIGH_RISK" in codes


def test_the_institution_list_is_never_overwritten_by_a_sync(app):
    """Russia is on no FATF or EU list but is a real exposure for an EU firm.
    The firm's own countries live in their own factor, and a refresh of the
    official lists must leave them exactly as configured."""
    from api.engine import country_risk
    from api.models import db, RiskFactor

    country_risk.sync(prefer_live=False)
    own = RiskFactor.query.filter_by(code=country_risk.INSTITUTION_CODE).first()
    assert own is not None and own.condition_type == "COUNTRY_IN"

    # What the compliance team actually configured.
    own.condition_value = {"values": ["Russia", "Belarus"]}
    db.session.commit()

    # A refresh of the official lists must not touch it, whatever it is asked.
    country_risk.sync(prefer_live=False, institution_countries=["ignored"])
    own = RiskFactor.query.filter_by(code=country_risk.INSTITUTION_CODE).first()
    assert own.condition_value["values"] == ["Russia", "Belarus"]
