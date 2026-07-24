"""Onboarding IP fraud check (AbuseIPDB): reports a clear error without a key,
and a high abuse score raises an IP_FRAUD_SIGNAL alert on the spine."""
from conftest import auth


def _customer(client, token, name):
    return client.post("/api/customers", headers=auth(token),
                       json={"name": name, "customer_type": "INDIVIDUAL",
                             "country": "Luxembourg"}).get_json()["id"]


def test_ip_check_without_key_reports_clearly(client, tokens):
    officer = tokens["officer@test.io"]
    cid = _customer(client, officer, "Fraud NoKey Co")
    r = client.post(f"/api/customers/{cid}/ip-check", headers=auth(officer),
                    json={"ip": "8.8.8.8"})
    assert r.status_code == 409
    assert "key" in r.get_json()["message"].lower()


def test_high_abuse_score_raises_alert(client, tokens, monkeypatch):
    from api.integrations.fraud.abuseipdb import AbuseIPDBProvider
    monkeypatch.setattr(AbuseIPDBProvider, "check", lambda self, ip, **k: {
        "ip": ip, "abuse_score": 90, "total_reports": 42, "country": "RU",
        "is_tor": True, "usage_type": "Data Center/Web Hosting/Transit",
        "isp": "Example", "domain": "example.net"})

    officer = tokens["officer@test.io"]
    cid = _customer(client, officer, "Fraud Signal Co")
    r = client.post(f"/api/customers/{cid}/ip-check", headers=auth(officer),
                    json={"ip": "1.2.3.4"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["risky"] is True
    assert body["result"]["abuse_score"] == 90

    alerts = client.get("/api/alerts", headers=auth(officer)).get_json()
    assert any(a["customer_id"] == cid and a["alert_type"] == "IP_FRAUD_SIGNAL"
               for a in alerts)


def test_clean_ip_no_alert(client, tokens, monkeypatch):
    from api.integrations.fraud.abuseipdb import AbuseIPDBProvider
    monkeypatch.setattr(AbuseIPDBProvider, "check", lambda self, ip, **k: {
        "ip": ip, "abuse_score": 0, "total_reports": 0, "country": "LU",
        "is_tor": False, "usage_type": "Fixed Line ISP", "isp": "X", "domain": "x.lu"})
    officer = tokens["officer@test.io"]
    cid = _customer(client, officer, "Fraud Clean Co")
    r = client.post(f"/api/customers/{cid}/ip-check", headers=auth(officer),
                    json={"ip": "5.6.7.8"})
    assert r.get_json()["risky"] is False
