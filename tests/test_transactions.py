"""Transaction monitoring: ingestion, idempotency, each detector, and the
TRANSACTION_ALERT -> case/alert chain on the compliance spine."""
from conftest import auth


def _company(client, token, name, country="Luxembourg"):
    return client.post("/api/customers", headers=auth(token),
                       json={"name": name, "customer_type": "COMPANY",
                             "country": country}).get_json()["id"]


def _ingest(client, token, cid, **tx):
    return client.post(f"/api/customers/{cid}/transactions",
                       headers=auth(token), json=tx)


def test_large_amount_flags_and_raises_alert(client, tokens):
    t = tokens["officer@test.io"]
    cid = _company(client, t, "TM Large Co")
    r = _ingest(client, t, cid, direction="INBOUND", amount=25000, currency="EUR",
                counterparty_name="Acme", method="SWIFT")
    assert r.status_code == 201
    body = r.get_json()
    assert body["flagged"] == 1
    assert "LARGE_AMOUNT" in body["transactions"][0]["flags"]

    # The spine raised one alert of type TRANSACTION_ALERT for the customer.
    alerts = client.get("/api/alerts", headers=auth(t)).get_json()
    mine = [a for a in alerts if a["customer_id"] == cid
            and a["alert_type"] == "TRANSACTION_ALERT"]
    assert len(mine) == 1


def test_idempotent_on_external_id(client, tokens):
    t = tokens["officer@test.io"]
    cid = _company(client, t, "TM Idem Co")
    _ingest(client, t, cid, external_id="TX-1", amount=100, currency="EUR")
    _ingest(client, t, cid, external_id="TX-1", amount=100, currency="EUR")
    rows = client.get(f"/api/customers/{cid}/transactions",
                      headers=auth(t)).get_json()
    assert len([x for x in rows if x["external_id"] == "TX-1"]) == 1


def test_high_risk_counterparty_country(client, tokens, app):
    t = tokens["officer@test.io"]
    cid = _company(client, t, "TM HRC Co")
    # High-risk countries are data-driven (a COUNTRY_IN factor installed by
    # country_risk.sync in real deployments) — reflect that, don't hard-code.
    with app.app_context():
        from api.models import db, RiskMethodology, RiskFactor
        meth = RiskMethodology.query.filter_by(active=True).first()
        db.session.add(RiskFactor(
            methodology_id=meth.id, code="GEO_TEST", label="High-risk geo",
            impact=20, condition_type="COUNTRY_IN",
            condition_value={"values": ["Iran", "North Korea"]}, active=True))
        db.session.commit()
    r = _ingest(client, t, cid, amount=500, currency="EUR",
                counterparty_country="Iran")
    assert "HIGH_RISK_COUNTRY" in r.get_json()["transactions"][0]["flags"]


def test_structuring_needs_a_pattern_not_a_single_tx(client, tokens):
    t = tokens["officer@test.io"]
    cid = _company(client, t, "TM Struct Co")
    # Two just-under movements: below the count threshold (3) -> no flag yet.
    _ingest(client, t, cid, direction="INBOUND", amount=9000, currency="EUR")
    r2 = _ingest(client, t, cid, direction="INBOUND", amount=9200, currency="EUR")
    assert "STRUCTURING" not in r2.get_json()["transactions"][0]["flags"]
    # The third one completes the pattern.
    r3 = _ingest(client, t, cid, direction="INBOUND", amount=9500, currency="EUR")
    assert "STRUCTURING" in r3.get_json()["transactions"][0]["flags"]


def test_clean_transaction_is_not_flagged(client, tokens):
    t = tokens["officer@test.io"]
    cid = _company(client, t, "TM Clean Co")
    r = _ingest(client, t, cid, direction="INBOUND", amount=250, currency="EUR",
                counterparty_country="Luxembourg", method="SEPA")
    assert r.get_json()["transactions"][0]["flagged"] is False


def test_ingest_requires_permission(client, tokens):
    t_analyst = tokens["analyst@test.io"]
    cid = _company(client, tokens["officer@test.io"], "TM Perm Co")
    # Auditor can view but not ingest.
    r = client.post(f"/api/customers/{cid}/transactions",
                    headers=auth(tokens["outsider@test.io"]),
                    json={"amount": 10})
    assert r.status_code in (403, 404)  # outsider: another org
