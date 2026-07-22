"""Team chat: DM dedupe, groups, membership boundaries, messages, unread,
uploads (local fallback), and the Socket.IO layer (auth + realtime delivery).
"""
import io

from conftest import auth


def _uid(client, admin_token, email):
    users = client.get("/api/users", headers=auth(admin_token)).get_json()
    return next(u["id"] for u in users if u["email"] == email)


def test_dm_created_once_and_reused(client, tokens):
    ta = tokens["analyst@test.io"]
    officer_id = _uid(client, tokens["admin@test.io"], "officer@test.io")

    r1 = client.post("/api/chat/rooms", headers=auth(ta),
                     json={"user_id": officer_id})
    assert r1.status_code == 201
    room = r1.get_json()
    assert room["is_group"] is False
    assert room["display_name"] == "O"  # officer's full_name in the seed

    # Same DM again -> reused, not duplicated (200, same id).
    r2 = client.post("/api/chat/rooms", headers=auth(ta),
                     json={"user_id": officer_id})
    assert r2.status_code == 200 and r2.get_json()["id"] == room["id"]


def test_group_message_flow_and_unread(client, tokens):
    ta = tokens["analyst@test.io"]
    to = tokens["officer@test.io"]
    admin = tokens["admin@test.io"]
    officer_id = _uid(client, admin, "officer@test.io")
    manager_id = _uid(client, admin, "manager@test.io")

    room = client.post("/api/chat/rooms", headers=auth(ta),
                       json={"name": "Case war-room",
                             "member_ids": [officer_id, manager_id]}).get_json()
    assert room["is_group"] and len(room["members"]) == 3

    r = client.post(f"/api/chat/rooms/{room['id']}/messages", headers=auth(ta),
                    json={"body": "Sberbank case: sync at 3pm?"})
    assert r.status_code == 201

    # The officer sees 1 unread; after marking read it drops to 0.
    rooms = client.get("/api/chat/rooms", headers=auth(to)).get_json()
    mine = next(x for x in rooms if x["id"] == room["id"])
    assert mine["unread"] == 1
    assert mine["last_message"]["body"].startswith("Sberbank")
    client.post(f"/api/chat/rooms/{room['id']}/read", headers=auth(to))
    rooms = client.get("/api/chat/rooms", headers=auth(to)).get_json()
    assert next(x for x in rooms if x["id"] == room["id"])["unread"] == 0

    msgs = client.get(f"/api/chat/rooms/{room['id']}/messages",
                      headers=auth(to)).get_json()
    assert msgs[-1]["sender_name"] == "A"


def test_non_member_cannot_read_or_post(client, tokens):
    ta = tokens["analyst@test.io"]
    outsider = tokens["outsider@test.io"]       # other org
    manager = tokens["manager@test.io"]         # same org, not a member
    admin = tokens["admin@test.io"]
    officer_id = _uid(client, admin, "officer@test.io")

    room = client.post("/api/chat/rooms", headers=auth(ta),
                       json={"user_id": officer_id}).get_json()
    for tok in (outsider, manager):
        assert client.get(f"/api/chat/rooms/{room['id']}/messages",
                          headers=auth(tok)).status_code == 404
        assert client.post(f"/api/chat/rooms/{room['id']}/messages",
                           headers=auth(tok),
                           json={"body": "hi"}).status_code == 404

    # Cross-org DM creation is rejected outright.
    outsider_me = client.get("/api/auth/me", headers=auth(outsider)).get_json()
    r = client.post("/api/chat/rooms", headers=auth(ta),
                    json={"user_id": outsider_me["user"]["id"]})
    assert r.status_code == 404


def test_upload_local_fallback_and_media_message(client, tokens, app):
    """Without CLOUDINARY_URL, files land on local disk and are served back."""
    ta = tokens["analyst@test.io"]
    admin = tokens["admin@test.io"]
    officer_id = _uid(client, admin, "officer@test.io")
    room = client.post("/api/chat/rooms", headers=auth(ta),
                       json={"user_id": officer_id}).get_json()

    data = {"file": (io.BytesIO(b"fake-voice-note"), "note.webm", "audio/webm")}
    up = client.post("/api/chat/upload", headers=auth(ta), data=data,
                     content_type="multipart/form-data")
    assert up.status_code == 201
    stored = up.get_json()
    assert stored["provider"] == "local" and stored["kind"] == "AUDIO"
    assert stored["url"].startswith("/api/media/")

    r = client.post(f"/api/chat/rooms/{room['id']}/messages", headers=auth(ta),
                    json={"kind": stored["kind"], "media_url": stored["url"],
                          "media_type": stored["media_type"]})
    assert r.status_code == 201 and r.get_json()["kind"] == "AUDIO"

    # The raw path is not public anymore; a signed URL serves it.
    from api.integrations import media
    assert client.get(stored["url"]).status_code == 403
    got = client.get(media.sign_url(stored["url"]))
    assert got.status_code == 200 and got.data == b"fake-voice-note"


def test_upload_falls_back_to_local_when_cloudinary_broken(client, tokens,
                                                           monkeypatch):
    """A placeholder/invalid CLOUDINARY_URL (the Codespace bug) must never
    break chat media — the upload silently falls back to local storage."""
    import cloudinary.uploader

    def boom(*args, **kwargs):
        raise RuntimeError("Invalid api_key key")
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://placeholder:key@demo")
    monkeypatch.setattr(cloudinary.uploader, "upload", boom)

    t = tokens["analyst@test.io"]
    data = {"file": (io.BytesIO(b"pdf-bytes"), "report.pdf", "application/pdf")}
    r = client.post("/api/chat/upload", headers=auth(t), data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 201
    stored = r.get_json()
    assert stored["provider"] == "local"
    assert "Cloudinary failed" in stored.get("note", "")
    # The fallback file is intact and served through a signed URL.
    from api.integrations import media
    got = client.get(media.sign_url(stored["url"]))
    assert got.status_code == 200 and got.data == b"pdf-bytes"


def test_socket_auth_and_realtime_message(app, tokens):
    """A socket with a valid JWT connects and receives room broadcasts;
    a bad token is rejected."""
    from api.sockets import socketio

    bad = socketio.test_client(app, auth={"token": "not-a-jwt"})
    assert not bad.is_connected()

    with app.app_context():
        from api.models import db, User, ChatRoom, ChatMember
        analyst = User.query.filter_by(email="analyst@test.io").first()
        officer = User.query.filter_by(email="officer@test.io").first()
        room = ChatRoom(organization_id=analyst.organization_id, is_group=True,
                        name="Socket room", created_by=analyst.id)
        db.session.add(room)
        db.session.flush()
        db.session.add_all([ChatMember(room_id=room.id, user_id=analyst.id),
                            ChatMember(room_id=room.id, user_id=officer.id)])
        db.session.commit()
        room_id = room.id

    a = socketio.test_client(app, auth={"token": tokens["analyst@test.io"]})
    o = socketio.test_client(app, auth={"token": tokens["officer@test.io"]})
    assert a.is_connected() and o.is_connected()

    a.emit("chat:send", {"room_id": room_id, "body": "realtime hello"})
    received = [p for p in o.get_received() if p["name"] == "chat:message"]
    assert received and received[-1]["args"][0]["body"] == "realtime hello"

    # Call signalling: analyst starts a call -> officer's socket rings.
    a.emit("call:start", {"room_id": room_id, "media": "video"})
    ringing = [p for p in o.get_received() if p["name"] == "call:ringing"]
    assert ringing and ringing[-1]["args"][0]["room_id"] == room_id
    a.emit("call:leave", {"room_id": room_id})
    a.disconnect(); o.disconnect()
