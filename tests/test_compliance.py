from conftest import auth


def _cid(client, token, name):
    r = client.get("/api/customers", headers=auth(token))
    return next(c["id"] for c in r.get_json() if c["name"] == name)


def test_screening_raises_case_alert_and_risk(client, tokens):
    t = tokens["analyst@test.io"]
    jid = _cid(client, t, "John Smith")
    client.post(f"/api/customers/{jid}/screen", headers=auth(t))
    d = client.get(f"/api/customers/{jid}", headers=auth(t)).get_json()

    assert d["customer"]["risk_level"] in ("MEDIUM", "HIGH", "CRITICAL")
    assert any(m["match_type"] == "SANCTIONS" for m in d["screening_matches"])
    assert any(c["case_type"] == "SANCTIONS_MATCH" for c in d["open_cases"])
    # a first-class alert was raised (distinct from a notification)
    assert any(a["alert_type"] == "SANCTIONS_MATCH_FOUND" for a in d["open_alerts"])


def test_false_positive_keeps_history_and_lowers_risk(client, tokens):
    ta, to = tokens["analyst@test.io"], tokens["officer@test.io"]
    jid = _cid(client, ta, "John Smith")
    client.post(f"/api/customers/{jid}/screen", headers=auth(ta))
    case = client.get("/api/cases?status=OPEN", headers=auth(ta)).get_json()[0]
    client.post(f"/api/cases/{case['id']}/decision",
                json={"decision": "FALSE_POSITIVE", "reason": "DOB mismatch"},
                headers=auth(to))
    d = client.get(f"/api/customers/{jid}", headers=auth(ta)).get_json()
    # the match record survives as FALSE_POSITIVE; the flag is cleared
    assert any(m["status"] == "FALSE_POSITIVE" for m in d["screening_matches"])
    assert d["customer"]["has_sanctions_match"] is False


def test_high_risk_pulls_in_edd_requirements(client, tokens):
    to = tokens["officer@test.io"]
    # A sanctions+PEP hit in a high-risk jurisdiction reaches HIGH risk, which
    # pulls in the EDD requirements (Source of Wealth / Funds). Iran rather
    # than Russia: geography now scores off the FATF/EU lists, and Russia is on
    # neither (it belongs in the institution's own list, not a regulator's).
    from api.engine import country_risk
    country_risk.sync(prefer_live=False)
    created = client.post("/api/customers",
                          json={"name": "Ivan Ivanov", "country": "Iran",
                                "customer_type": "INDIVIDUAL"},
                          headers=auth(to)).get_json()
    cid = created["id"]
    client.post(f"/api/customers/{cid}/screen", headers=auth(to))
    d = client.get(f"/api/customers/{cid}", headers=auth(to)).get_json()
    assert d["customer"]["risk_level"] in ("HIGH", "CRITICAL")
    codes = {r["code"] for r in d["completeness"]["requirements"]}
    assert "SOURCE_OF_WEALTH" in codes and "SOURCE_OF_FUNDS" in codes


def test_missing_information_request_creates_tasks(client, tokens):
    ta = tokens["analyst@test.io"]
    mid = _cid(client, ta, "Marie Dupont")
    r = client.post(f"/api/customers/{mid}/request-info", headers=auth(ta))
    assert r.status_code == 202
    body = r.get_json()
    assert body["created"] > 0 and body["missing"] == body["created"]
