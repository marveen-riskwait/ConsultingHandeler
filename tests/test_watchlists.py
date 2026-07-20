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
