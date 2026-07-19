import hashlib
import hmac
import json

from conftest import auth

WEBHOOK = "/api/webhooks/providers/Mock%20Identity"
SECRET = b"demo-secret"


def _sig(body: bytes):
    return "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()


def test_webhook_rejects_bad_signature(client):
    body = json.dumps({"event_id": "bad1", "status": "FAILED"}).encode()
    r = client.post(WEBHOOK, data=body, content_type="application/json",
                    headers={"X-Signature": "sha256=deadbeef"})
    assert r.status_code == 401


def test_webhook_accepts_valid_signature_and_is_idempotent(client):
    payload = {"event_id": "ok1", "reviewResult": {"reviewAnswer": "RED"},
               "applicantId": "a1", "type": "IDENTITY"}
    body = json.dumps(payload).encode()
    h = {"X-Signature": _sig(body)}

    r = client.post(WEBHOOK, data=body, content_type="application/json", headers=h)
    assert r.status_code == 200 and r.get_json()["status"] == "processed"

    r = client.post(WEBHOOK, data=body, content_type="application/json", headers=h)
    assert r.status_code == 200 and r.get_json()["status"] == "duplicate"


def test_rejected_webhook_does_not_block_the_event_id(client):
    payload = {"event_id": "reuse1", "reviewResult": {"reviewAnswer": "RED"},
               "applicantId": "a2", "type": "IDENTITY"}
    body = json.dumps(payload).encode()
    # first attempt has a bad signature (rejected)…
    client.post(WEBHOOK, data=body, content_type="application/json",
                headers={"X-Signature": "sha256=bad"})
    # …the legitimate retry with the same event_id must still process.
    r = client.post(WEBHOOK, data=body, content_type="application/json",
                    headers={"X-Signature": _sig(body)})
    assert r.get_json()["status"] == "processed"


def test_provider_credentials_never_leak(client, tokens):
    r = client.get("/api/providers", headers=auth(tokens["admin@test.io"]))
    assert r.status_code == 200
    assert "demo-secret" not in r.get_data(as_text=True)
