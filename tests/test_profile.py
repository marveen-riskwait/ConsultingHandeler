"""The profile: identity a user edits, the security they manage themselves."""
import io

from conftest import auth


def test_a_user_edits_their_own_identity(client, tokens):
    to = tokens["officer@test.io"]
    r = client.patch("/api/profile", headers=auth(to),
                     json={"full_name": "Olivia Officer", "job_title": "MLRO",
                           "phone": "+352 123 456", "timezone": "Europe/Luxembourg"})
    assert r.status_code == 200
    u = r.get_json()["user"]
    assert u["full_name"] == "Olivia Officer" and u["job_title"] == "MLRO"

    me = client.get("/api/auth/me", headers=auth(to)).get_json()["user"]
    assert me["phone"] == "+352 123 456"


def test_profile_cannot_change_role_or_email(client, tokens):
    to = tokens["analyst@test.io"]
    client.patch("/api/profile", headers=auth(to),
                 json={"role": "ADMIN", "email": "hacker@evil.io",
                       "full_name": "Alex"})
    me = client.get("/api/auth/me", headers=auth(to)).get_json()["user"]
    assert me["role"] == "KYC_ANALYST" and me["email"] == "analyst@test.io"


def test_avatar_upload_only_accepts_images_and_signs_the_url(client, tokens):
    to = tokens["officer@test.io"]
    bad = client.post("/api/profile/avatar", headers=auth(to),
                      data={"file": (io.BytesIO(b"%PDF"), "cv.pdf", "application/pdf")},
                      content_type="multipart/form-data")
    assert bad.status_code == 400

    ok = client.post("/api/profile/avatar", headers=auth(to),
                     data={"file": (io.BytesIO(b"\\x89PNG face"), "me.png", "image/png")},
                     content_type="multipart/form-data")
    assert ok.status_code == 200
    url = ok.get_json()["user"]["avatar_url"]
    assert "sig=" in url                                  # signed like documents
    assert client.get(url).status_code == 200             # and served
    key = url.split("/api/media/")[1].split("?")[0]
    assert client.get(f"/api/media/{key}").status_code == 403   # not public


def test_change_password_needs_the_current_one_and_applies_policy(client, tokens):
    to = tokens["manager@test.io"]
    # wrong current
    assert client.post("/api/profile/password", headers=auth(to),
                       json={"current_password": "nope",
                             "new_password": "Brand-New-Passw0rd"}).status_code == 400
    # weak new
    assert client.post("/api/profile/password", headers=auth(to),
                       json={"current_password": "pw",
                             "new_password": "short"}).status_code == 400
    # ok
    assert client.post("/api/profile/password", headers=auth(to),
                       json={"current_password": "pw",
                             "new_password": "Brand-New-Passw0rd"}).status_code == 200
    assert client.post("/api/auth/login",
                       json={"email": "manager@test.io",
                             "password": "Brand-New-Passw0rd"}).status_code == 200


def test_the_avatar_appears_on_chat_messages(client, tokens, app):
    """The point of the photo: recognisable in chat before reading the name."""
    to = tokens["officer@test.io"]
    client.post("/api/profile/avatar", headers=auth(to),
                data={"file": (io.BytesIO(b"\\x89PNG"), "a.png", "image/png")},
                content_type="multipart/form-data")

    room = client.post("/api/chat/rooms", headers=auth(to),
                       json={"user_id": next(
                           u["id"] for u in client.get("/api/chat/users",
                                                       headers=auth(to)).get_json())}
                       ).get_json()
    client.post(f"/api/chat/rooms/{room['id']}/messages", headers=auth(to),
                json={"body": "hello"})
    msgs = client.get(f"/api/chat/rooms/{room['id']}/messages",
                      headers=auth(to)).get_json()
    assert msgs[-1]["sender_avatar"] and "sig=" in msgs[-1]["sender_avatar"]
