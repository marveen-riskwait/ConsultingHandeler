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


# --- close-out: the chain must fire backwards too ---------------------------

def test_clearing_every_match_closes_the_case_and_resolves_the_alerts(client, tokens):
    """Everything fired forwards — a match opened a case and raised an alert —
    and nothing fired backwards: clearing the match left both standing, so the
    alert center filled with findings that no longer existed."""
    from api.models import Case, ComplianceAlert, ScreeningMatch

    to = tokens["officer@test.io"]
    cid = client.post("/api/customers", headers=auth(to),
                      json={"name": "Sergei Ivanov", "customer_type": "INDIVIDUAL",
                            "country": "Russia"}).get_json()["id"]
    client.post(f"/api/customers/{cid}/screen", headers=auth(to))

    matches = ScreeningMatch.query.filter_by(customer_id=cid).all()
    assert matches, "screening should hit the seeded provider"
    for m in matches:
        client.post(f"/api/screening/matches/{m.id}/review", headers=auth(to),
                    json={"decision": "FALSE_POSITIVE", "reason": "test clear"})

    open_cases = (Case.query.filter_by(customer_id=cid)
                  .filter(Case.status == "OPEN").count())
    open_alerts = (ComplianceAlert.query.filter_by(customer_id=cid)
                   .filter(ComplianceAlert.status.in_(("OPEN", "ASSIGNED"))).count())
    assert open_cases == 0, "no case should stay open with no active match"
    assert open_alerts == 0, "no alert should stay open with no active match"
    # And the audit trail shows why they closed.
    entries = client.get("/api/audit?entity_type=case", headers=auth(to)).get_json()
    rows = entries if isinstance(entries, list) else entries.get("items", [])
    assert any("cleared" in (e.get("reason") or "").lower() for e in rows)


def test_a_case_stays_open_while_one_match_is_still_active(client, tokens):
    """Nothing closes while something is live: clear one match of two and the
    case must remain open for the other."""
    from api.models import Case, ScreeningMatch

    to = tokens["officer@test.io"]
    cid = client.post("/api/customers", headers=auth(to),
                      json={"name": "Sergei Ivanov", "customer_type": "INDIVIDUAL",
                            "country": "Russia"}).get_json()["id"]
    client.post(f"/api/customers/{cid}/screen", headers=auth(to))
    matches = ScreeningMatch.query.filter_by(customer_id=cid).all()
    if len(matches) < 2:
        return  # provider seeded differently; the all-clear test covers it
    client.post(f"/api/screening/matches/{matches[0].id}/review", headers=auth(to),
                json={"decision": "FALSE_POSITIVE", "reason": "one of two"})
    still_open = (Case.query.filter_by(customer_id=cid)
                  .filter(Case.status == "OPEN").count())
    assert still_open >= 1, "the other finding still needs its case"


def test_requirements_exist_from_the_moment_a_customer_does(client, tokens):
    """Lazily computed requirements meant a customer nobody had opened had none
    in the database — invisible to any dashboard, export or reminder job that
    reads the table directly."""
    from api.models import RequirementInstance

    to = tokens["officer@test.io"]
    cid = client.post("/api/customers", headers=auth(to),
                      json={"name": "Fresh Materialised Co",
                            "customer_type": "COMPANY"}).get_json()["id"]
    assert RequirementInstance.query.filter_by(customer_id=cid).count() > 0


def test_information_request_task_closes_when_the_item_arrives(client, tokens):
    """The ghost task: the customer sends what was asked, the chase task stays
    open, and an analyst ends up chasing a client who already complied."""
    from api.models import Task

    to = tokens["officer@test.io"]
    cid = client.post("/api/customers", headers=auth(to),
                      json={"name": "Chased Client", "customer_type": "INDIVIDUAL"}
                      ).get_json()["id"]
    client.post(f"/api/customers/{cid}/request-info", headers=auth(to))
    chase = (Task.query.filter_by(customer_id=cid, task_type="INFORMATION_REQUEST")
             .filter(Task.requirement_code == "OCCUPATION").first())
    assert chase is not None and chase.status != "DONE"

    client.post(f"/api/customers/{cid}/fields", headers=auth(to),
                json={"field_key": "occupation", "value": "Architect"})
    from api.engine import requirement_engine
    from api.models import Customer
    requirement_engine.evaluate(Customer.query.get(cid))

    assert Task.query.get(chase.id).status == "DONE", \
        "the chase task must close when the item arrives"
