"""Real-time layer: chat messaging + WebRTC call signalling over Socket.IO.

Runs in threading mode (no eventlet/gevent); simple-websocket provides real
WebSocket upgrades on the dev server, and clients fall back to long-polling
automatically elsewhere.

Auth: the client passes its JWT in the connection `auth` payload; every event
handler resolves the user from the socket session. Room access is always
re-checked against ChatMember — the socket layer enforces the same boundaries
as the REST API.

Call model: WebRTC mesh. The server never sees media — it only relays
offers/answers/ICE between peers (`webrtc:signal`) and tracks who is in the
call so ringing/join/leave stay consistent. Call state is in-process, which is
fine for a single web process (the Fly/demo topology).
"""
from collections import defaultdict

from flask import request
from flask_socketio import SocketIO, join_room, leave_room, emit
from flask_jwt_extended import decode_token

socketio = SocketIO()

# sid -> user_id for this process; call participants per room.
_SID_USERS = {}
_CALLS = defaultdict(dict)   # room_id -> {user_id: {"name":…, "media":…}}


def _user_from_sid():
    from api.models import User
    uid = _SID_USERS.get(request.sid)
    return User.query.get(uid) if uid else None


def _member(user, room_id):
    from api.models import ChatMember
    if user is None:
        return None
    return ChatMember.query.filter_by(room_id=room_id, user_id=user.id).first()


def _room_channel(room_id):
    return f"chatroom:{room_id}"


def _user_channel(user_id):
    return f"user:{user_id}"


@socketio.on("connect")
def on_connect(auth):
    from api.models import User, ChatMember
    token = (auth or {}).get("token")
    try:
        uid = int(decode_token(token)["sub"])
    except Exception:
        return False  # reject the connection
    user = User.query.get(uid)
    if user is None or not user.is_active:
        return False

    _SID_USERS[request.sid] = user.id
    join_room(_user_channel(user.id))
    for m in ChatMember.query.filter_by(user_id=user.id).all():
        join_room(_room_channel(m.room_id))
    return True


@socketio.on("disconnect")
def on_disconnect():
    uid = _SID_USERS.pop(request.sid, None)
    if uid is None:
        return
    # Drop the user from any call they were in and tell the others.
    for room_id, participants in list(_CALLS.items()):
        if uid in participants:
            participants.pop(uid, None)
            emit("call:peer-left", {"room_id": room_id, "user_id": uid},
                 to=_room_channel(room_id))
            if not participants:
                _end_call(room_id)


# ------------------------------------------------------------------ messaging
@socketio.on("chat:send")
def on_chat_send(data):
    from api.models import db, ChatMessage, CHAT_MESSAGE_KINDS
    user = _user_from_sid()
    room_id = (data or {}).get("room_id")
    member = _member(user, room_id)
    if member is None:
        return
    kind = data.get("kind") or "TEXT"
    if kind not in CHAT_MESSAGE_KINDS:
        kind = "TEXT"
    body = (data.get("body") or "").strip() or None
    media_url = data.get("media_url")
    if not body and not media_url:
        return
    msg = ChatMessage(room_id=room_id, sender_id=user.id, kind=kind, body=body,
                      media_url=media_url, media_type=data.get("media_type"),
                      meta=data.get("meta") or {})
    db.session.add(msg)
    db.session.commit()
    emit("chat:message", msg.serialize(), to=_room_channel(room_id))


@socketio.on("chat:typing")
def on_chat_typing(data):
    user = _user_from_sid()
    room_id = (data or {}).get("room_id")
    if _member(user, room_id) is None:
        return
    emit("chat:typing",
         {"room_id": room_id, "user_id": user.id,
          "name": user.full_name or user.email},
         to=_room_channel(room_id), include_self=False)


@socketio.on("chat:read")
def on_chat_read(data):
    from api.models import db, utcnow
    user = _user_from_sid()
    member = _member(user, (data or {}).get("room_id"))
    if member is None:
        return
    member.last_read_at = utcnow()
    db.session.commit()


@socketio.on("chat:join-room")
def on_chat_join_room(data):
    """Subscribe this socket to a room created after connect time."""
    user = _user_from_sid()
    room_id = (data or {}).get("room_id")
    if _member(user, room_id) is not None:
        join_room(_room_channel(room_id))


# ---------------------------------------------------------------------- calls
def _system_message(room_id, text):
    from api.models import db, ChatMessage
    msg = ChatMessage(room_id=room_id, sender_id=None, kind="SYSTEM", body=text)
    db.session.add(msg)
    db.session.commit()
    emit("chat:message", msg.serialize(), to=_room_channel(room_id))


def _end_call(room_id):
    _CALLS.pop(room_id, None)
    emit("call:ended", {"room_id": room_id}, to=_room_channel(room_id))
    _system_message(room_id, "Call ended")


@socketio.on("call:start")
def on_call_start(data):
    """First participant opens a call in a room -> ring everyone else."""
    user = _user_from_sid()
    room_id = (data or {}).get("room_id")
    if _member(user, room_id) is None:
        return
    media = "video" if (data or {}).get("media") == "video" else "audio"
    _CALLS[room_id][user.id] = {"name": user.full_name or user.email,
                                "media": media}
    _system_message(room_id, f"{user.full_name or user.email} started a "
                             f"{media} call")
    emit("call:ringing",
         {"room_id": room_id, "from_id": user.id, "media": media,
          "from_name": user.full_name or user.email},
         to=_room_channel(room_id), include_self=False)
    emit("call:participants",
         {"room_id": room_id,
          "participants": [{"user_id": k, **v}
                           for k, v in _CALLS[room_id].items()]},
         to=request.sid)


@socketio.on("call:join")
def on_call_join(data):
    """Late joiner: gets the current roster and OFFERS to each existing peer."""
    user = _user_from_sid()
    room_id = (data or {}).get("room_id")
    if _member(user, room_id) is None or room_id not in _CALLS:
        return
    existing = [{"user_id": k, **v} for k, v in _CALLS[room_id].items()]
    _CALLS[room_id][user.id] = {"name": user.full_name or user.email,
                                "media": (data or {}).get("media", "video")}
    emit("call:participants", {"room_id": room_id, "participants": existing},
         to=request.sid)
    emit("call:peer-joined",
         {"room_id": room_id, "user_id": user.id,
          "name": user.full_name or user.email},
         to=_room_channel(room_id), include_self=False)


@socketio.on("call:leave")
def on_call_leave(data):
    user = _user_from_sid()
    room_id = (data or {}).get("room_id")
    if user is None or room_id not in _CALLS:
        return
    _CALLS[room_id].pop(user.id, None)
    emit("call:peer-left", {"room_id": room_id, "user_id": user.id},
         to=_room_channel(room_id))
    if not _CALLS[room_id]:
        _end_call(room_id)


@socketio.on("webrtc:signal")
def on_webrtc_signal(data):
    """Relay an SDP offer/answer or ICE candidate to one peer, untouched."""
    user = _user_from_sid()
    data = data or {}
    room_id, to_id = data.get("room_id"), data.get("to")
    if _member(user, room_id) is None or not to_id:
        return
    emit("webrtc:signal",
         {"room_id": room_id, "from": user.id, "data": data.get("data")},
         to=_user_channel(int(to_id)))
