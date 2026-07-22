"""Media is not public: only a short-lived signed URL serves a file.

The old route served any file to anyone who knew the UUID name — and customer
identity documents live there. Now a file is reachable only through a signed,
expiring URL that serializers mint for already-authorized users.
"""
import io
import time

from conftest import auth


def _upload_doc(client, token, cid):
    return client.post(f"/api/customers/{cid}/documents", headers=auth(token),
                       data={"doc_type": "PASSPORT",
                             "file": (io.BytesIO(b"%PDF secret id"), "id.pdf",
                                      "application/pdf")},
                       content_type="multipart/form-data").get_json()


def test_raw_media_url_is_refused_without_a_signature(client, tokens):
    to = tokens["officer@test.io"]
    cid = next(c["id"] for c in
               client.get("/api/customers", headers=auth(to)).get_json())
    doc = _upload_doc(client, to, cid)
    # The serialized file_url is signed…
    assert "sig=" in doc["file_url"] and "exp=" in doc["file_url"]

    # …and the bare path (what an attacker who guessed the name would try) is
    # refused.
    key = doc["file_url"].split("/api/media/")[1].split("?")[0]
    assert client.get(f"/api/media/{key}").status_code == 403
    # A tampered signature is refused too.
    assert client.get(f"/api/media/{key}?exp=9999999999&sig=deadbeef").status_code == 403


def test_a_valid_signed_url_serves_the_file(client, tokens):
    to = tokens["officer@test.io"]
    cid = next(c["id"] for c in
               client.get("/api/customers", headers=auth(to)).get_json())
    doc = _upload_doc(client, to, cid)
    r = client.get(doc["file_url"])         # the signed URL as delivered
    assert r.status_code == 200 and b"secret id" in r.data


def test_a_signed_url_expires(client, tokens, monkeypatch):
    from api.integrations import media
    to = tokens["officer@test.io"]
    cid = next(c["id"] for c in
               client.get("/api/customers", headers=auth(to)).get_json())
    doc = _upload_doc(client, to, cid)
    signed = doc["file_url"]

    # Jump past the TTL: the same URL no longer serves.
    real = time.time
    monkeypatch.setattr(media.time, "time",
                        lambda: real() + media.SIGNED_URL_TTL + 5)
    assert client.get(signed).status_code == 403


def test_signature_is_bound_to_the_specific_file(client, tokens):
    """A signature for one file must not open another."""
    to = tokens["officer@test.io"]
    cid = next(c["id"] for c in
               client.get("/api/customers", headers=auth(to)).get_json())
    a = _upload_doc(client, to, cid)
    b = _upload_doc(client, to, cid)
    a_sig = a["file_url"].split("?")[1]
    b_key = b["file_url"].split("/api/media/")[1].split("?")[0]
    # b's key with a's signature -> refused.
    assert client.get(f"/api/media/{b_key}?{a_sig}").status_code == 403
