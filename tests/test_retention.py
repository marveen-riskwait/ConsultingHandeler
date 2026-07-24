"""GDPR: subject-access export and retention purge (archived customers past
the retention period are erased; recent ones are kept; the audit trail
survives)."""
from datetime import timedelta
from conftest import auth


def _customer(client, token, name):
    return client.post("/api/customers", headers=auth(token),
                       json={"name": name, "customer_type": "INDIVIDUAL",
                             "country": "Luxembourg"}).get_json()["id"]


def test_data_export_returns_the_subject_file(client, tokens):
    officer = tokens["officer@test.io"]
    cid = _customer(client, officer, "Export Subject")
    r = client.get(f"/api/customers/{cid}/data-export", headers=auth(officer))
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    body = r.get_json()
    assert body["customer"]["name"] == "Export Subject"
    # The expected sections are present (even if empty).
    for key in ("profile_fields", "documents", "cases", "transactions",
                "reports", "audit_trail"):
        assert key in body


def test_export_requires_permission(client, tokens):
    officer = tokens["officer@test.io"]
    cid = _customer(client, officer, "Export Perm")
    # Analyst lacks data.export.
    r = client.get(f"/api/customers/{cid}/data-export",
                   headers=auth(tokens["analyst@test.io"]))
    assert r.status_code == 403


def test_purge_erases_only_customers_past_retention(client, tokens, app):
    officer = tokens["officer@test.io"]
    old_id = _customer(client, officer, "Old Archived Co")
    recent_id = _customer(client, officer, "Recent Archived Co")

    # Set a 12-month retention, archive both, then backdate one past the window.
    client.put("/api/retention/policy", headers=auth(officer), json={"months": 12})
    for cid in (old_id, recent_id):
        client.post(f"/api/customers/{cid}/archive", headers=auth(officer),
                    json={"reason": "Relationship ended"})
    with app.app_context():
        from api.models import db, Customer, utcnow
        c = Customer.query.get(old_id)
        c.archived_at = utcnow() - timedelta(days=400)   # > 12 months
        db.session.commit()

    dash = client.get("/api/retention", headers=auth(officer)).get_json()
    ids = {c["id"] for c in dash["candidates"]}
    assert old_id in ids and recent_id not in ids

    res = client.post("/api/retention/purge", headers=auth(officer),
                      json={}).get_json()
    assert res["count"] == 1
    assert client.get(f"/api/customers/{old_id}", headers=auth(officer)).status_code == 404
    assert client.get(f"/api/customers/{recent_id}", headers=auth(officer)).status_code == 200


def test_purge_requires_permission(client, tokens):
    r = client.post("/api/retention/purge",
                    headers=auth(tokens["analyst@test.io"]), json={})
    assert r.status_code == 403
