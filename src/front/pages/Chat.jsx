import { useCallback, useEffect, useRef, useState } from "react";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";
import { getSocket } from "../services/socket";
import { FilePreview } from "../components/FilePreview";

const BASE = "";   // same-origin (Vite proxy / Flask-served bundle)
const mediaSrc = (url) => (url && url.startsWith("/") ? `${BASE}${url}` : url);
const ICE = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };

// Mirror of the backend's MIME -> message-kind mapping (for previews).
const kindFor = (mime) => {
  if ((mime || "").startsWith("image/")) return "IMAGE";
  if ((mime || "").startsWith("audio/")) return "AUDIO";
  if ((mime || "").startsWith("video/")) return "VIDEO";
  return "FILE";
};

const fmtSize = (bytes) => {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const fmtTime = (iso) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

// ------------------------------------------------------------ message bubble
const Bubble = ({ m, mine, onPreview, asOrganization }) => {
  if (m.kind === "SYSTEM" || m.kind === "CALL") {
    return <div className="ch-system">{m.body}</div>;
  }
  const open = () => onPreview({
    url: mediaSrc(m.media_url), mediaType: m.media_type,
    name: m.meta?.filename || m.body || "Attachment",
  });
  return (
    <div className={`ch-msg ${mine ? "mine" : ""}`}>
      <div className="ch-bubble">
        {!mine && m.sender_avatar && !asOrganization && (
          <img src={m.sender_avatar} alt="" className="pf-avatar ch-avatar" />
        )}
        {!mine && (
          <div className="ch-sender">
            {/* A client is answered by the organization, never by a named
                individual — while staff keep seeing which colleague wrote it. */}
            {asOrganization && m.from_staff
              ? (m.organization_name || "Compliance team")
              : m.sender_name}
          </div>
        )}
        {m.kind === "TEXT" && <span>{m.body}</span>}
        {m.kind === "AUDIO" && (
          <audio controls src={mediaSrc(m.media_url)} className="ch-audio" />
        )}
        {m.kind === "VIDEO" && (
          <video controls src={mediaSrc(m.media_url)} className="ch-video" />
        )}
        {m.kind === "IMAGE" && (
          <img src={mediaSrc(m.media_url)} alt="" className="ch-image"
            style={{ cursor: "zoom-in" }} onClick={open} />
        )}
        {m.kind === "FILE" && (
          <button type="button" className="ch-file" onClick={open}>
            <i className="fa-solid fa-file-lines" /> {m.body || "Attachment"}
            <span className="ch-file-hint">open</span>
          </button>
        )}
        {m.body && m.kind !== "TEXT" && m.kind !== "FILE" && (
          <div style={{ marginTop: ".25rem" }}>{m.body}</div>
        )}
        <span className="ch-time">{fmtTime(m.created_at)}</span>
      </div>
    </div>
  );
};

// ------------------------------------------------------------------ the page
export const Chat = () => {
  const { store } = useGlobalReducer();
  const myId = store.user?.id;

  const [rooms, setRooms] = useState([]);
  const [colleagues, setColleagues] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [typing, setTyping] = useState(null);
  const [showNew, setShowNew] = useState(false);
  const [groupForm, setGroupForm] = useState({ name: "", ids: [] });
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState(null);
  // Staged attachment (voice note / file): previewed in the composer, sent
  // only when the user confirms — never immediately.
  const [pending, setPending] = useState(null); // {file, kind, url, name, size}
  const [sending, setSending] = useState(false);
  const [preview, setPreview] = useState(null);  // in-platform file viewer
  const [roomSearch, setRoomSearch] = useState("");
  const [dirSearch, setDirSearch] = useState("");

  // Call state. peersRef: user_id -> RTCPeerConnection.
  const [call, setCall] = useState(null);          // {roomId, media, joined}
  const [incoming, setIncoming] = useState(null);  // {room_id, from_name, media}
  const [remoteStreams, setRemoteStreams] = useState({}); // uid -> MediaStream
  const [micOn, setMicOn] = useState(true);
  const [camOn, setCamOn] = useState(true);
  const [hasMedia, setHasMedia] = useState(false); // false = viewer mode
  const localStreamRef = useRef(null);
  const localVideoRef = useRef(null);
  const peersRef = useRef({});
  const recorderRef = useRef(null);
  const scrollRef = useRef(null);
  const activeIdRef = useRef(null);
  activeIdRef.current = activeId;
  const callRef = useRef(null);
  callRef.current = call;

  const loadRooms = useCallback(() =>
    api.chatRooms().then(setRooms).catch((e) => setError(e.message)), []);

  const openRoom = useCallback((id) => {
    setActiveId(id);
    // Discard any staged attachment — it was meant for the previous room.
    setPending((p) => { if (p) URL.revokeObjectURL(p.url); return null; });
    api.chatMessages(id).then(setMessages).catch((e) => setError(e.message));
    api.markChatRead(id).then(loadRooms).catch(() => {});
  }, [loadRooms]);

  // ------------------------------------------------------------- WebRTC mesh
  const signal = (roomId, to, data) =>
    getSocket()?.emit("webrtc:signal", { room_id: roomId, to, data });

  const makePeer = useCallback((uid, roomId, initiator) => {
    if (peersRef.current[uid]) return peersRef.current[uid];
    const pc = new RTCPeerConnection(ICE);
    peersRef.current[uid] = pc;
    (localStreamRef.current?.getTracks() || []).forEach((t) =>
      pc.addTrack(t, localStreamRef.current));
    pc.onicecandidate = (e) => {
      if (e.candidate) signal(roomId, uid, { candidate: e.candidate });
    };
    pc.ontrack = (e) => {
      setRemoteStreams((s) => ({ ...s, [uid]: e.streams[0] }));
    };
    if (initiator) {
      (async () => {
        const offer = await pc.createOffer({ offerToReceiveAudio: true,
                                             offerToReceiveVideo: true });
        await pc.setLocalDescription(offer);
        signal(roomId, uid, { sdp: pc.localDescription });
      })();
    }
    return pc;
  }, []);

  const grabMedia = async (media) => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true, video: media === "video" });
      localStreamRef.current = stream;
      if (localVideoRef.current) localVideoRef.current.srcObject = stream;
      setHasMedia(true);
      setMicOn(true);
      setCamOn(media === "video");
      return stream;
    } catch (e) {
      setError("No camera/microphone available — joining as viewer.");
      setHasMedia(false);
      setMicOn(false);
      setCamOn(false);
      return null;
    }
  };

  const startCall = async (media) => {
    if (!activeId) return;
    await grabMedia(media);
    setCall({ roomId: activeId, media, joined: true });
    getSocket()?.emit("call:start", { room_id: activeId, media });
  };

  const acceptCall = async () => {
    const { room_id, media } = incoming;
    setIncoming(null);
    if (activeIdRef.current !== room_id) openRoom(room_id);
    await grabMedia(media);
    setCall({ roomId: room_id, media, joined: true });
    getSocket()?.emit("call:join", { room_id, media });
  };

  const hangup = useCallback((notify = true) => {
    const c = callRef.current;
    if (notify && c) getSocket()?.emit("call:leave", { room_id: c.roomId });
    Object.values(peersRef.current).forEach((pc) => pc.close());
    peersRef.current = {};
    (localStreamRef.current?.getTracks() || []).forEach((t) => t.stop());
    localStreamRef.current = null;
    setRemoteStreams({});
    setCall(null);
    setHasMedia(false);
    setMicOn(true);
    setCamOn(true);
  }, []);

  // Mute / camera-off: flip the outgoing track AND the button state together,
  // so the UI always reflects what peers actually receive.
  const toggleMic = () => {
    const tracks = (localStreamRef.current?.getAudioTracks() || []);
    if (!tracks.length) return;
    const next = !micOn;
    tracks.forEach((t) => { t.enabled = next; });
    setMicOn(next);
  };

  const toggleCam = () => {
    const tracks = (localStreamRef.current?.getVideoTracks() || []);
    if (!tracks.length) return;
    const next = !camOn;
    tracks.forEach((t) => { t.enabled = next; });
    setCamOn(next);
  };

  // -------------------------------------------------------- socket lifecycle
  useEffect(() => {
    const s = getSocket();
    if (!s) return undefined;

    const onMessage = (m) => {
      if (m.room_id === activeIdRef.current) {
        // Dedupe: our own sends are appended from the REST response too.
        setMessages((ms) => (ms.some((x) => x.id === m.id) ? ms : [...ms, m]));
        api.markChatRead(m.room_id).catch(() => {});
      }
      loadRooms();
    };
    const onRoomCreated = ({ room_id }) => {
      s.emit("chat:join-room", { room_id });
      loadRooms();
    };
    const onTyping = ({ room_id, name }) => {
      if (room_id === activeIdRef.current) {
        setTyping(name);
        setTimeout(() => setTyping(null), 2500);
      }
    };
    const onRinging = (data) => {
      if (callRef.current) return; // already on a call
      setIncoming(data);
    };
    const onParticipants = ({ room_id, participants }) => {
      // I just joined: offer to every existing peer.
      participants.forEach((p) => {
        if (p.user_id !== myId) makePeer(p.user_id, room_id, true);
      });
    };
    const onPeerLeft = ({ user_id }) => {
      peersRef.current[user_id]?.close();
      delete peersRef.current[user_id];
      setRemoteStreams((str) => {
        const next = { ...str };
        delete next[user_id];
        return next;
      });
    };
    const onEnded = ({ room_id }) => {
      if (callRef.current?.roomId === room_id) hangup(false);
      setIncoming((inc) => (inc?.room_id === room_id ? null : inc));
    };
    const onSignal = async ({ room_id, from, data }) => {
      if (!callRef.current || callRef.current.roomId !== room_id) return;
      const pc = makePeer(from, room_id, false);
      if (data?.sdp) {
        await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
        if (data.sdp.type === "offer") {
          const answer = await pc.createAnswer();
          await pc.setLocalDescription(answer);
          signal(room_id, from, { sdp: pc.localDescription });
        }
      } else if (data?.candidate) {
        try { await pc.addIceCandidate(new RTCIceCandidate(data.candidate)); }
        catch (e) { /* ignore late candidates */ }
      }
    };

    s.on("chat:message", onMessage);
    s.on("chat:room-created", onRoomCreated);
    s.on("chat:typing", onTyping);
    s.on("call:ringing", onRinging);
    s.on("call:participants", onParticipants);
    s.on("call:peer-left", onPeerLeft);
    s.on("call:ended", onEnded);
    s.on("webrtc:signal", onSignal);
    return () => {
      s.off("chat:message", onMessage);
      s.off("chat:room-created", onRoomCreated);
      s.off("chat:typing", onTyping);
      s.off("call:ringing", onRinging);
      s.off("call:participants", onParticipants);
      s.off("call:peer-left", onPeerLeft);
      s.off("call:ended", onEnded);
      s.off("webrtc:signal", onSignal);
    };
  }, [loadRooms, makePeer, myId, hangup, openRoom]);

  useEffect(() => {
    loadRooms();
    api.chatUsers().then(setColleagues).catch(() => {});
  }, [loadRooms]);

  // Arriving from a customer file (/chat?room=12) opens that conversation.
  useEffect(() => {
    const wanted = Number(new URLSearchParams(window.location.search).get("room"));
    if (wanted && rooms.some((r) => r.id === wanted) && activeId !== wanted) {
      openRoom(wanted);
    }
  }, [rooms, activeId, openRoom]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => () => hangup(), [hangup]); // leave call on unmount

  // ------------------------------------------------------------------ actions
  // Messages go over REST (guaranteed even when the WebSocket is degraded —
  // e.g. Codespace port-forwarding, or during heavy call signalling); the
  // backend broadcasts to everyone's sockets, and we append our own copy from
  // the response immediately (deduped when the echo arrives).
  const deliver = async (payload) => {
    setError(null);
    try {
      const m = await api.sendChatMessage(activeIdRef.current, payload);
      setMessages((ms) => (ms.some((x) => x.id === m.id) ? ms : [...ms, m]));
      loadRooms();
    } catch (e) { setError(e.message); }
  };

  // Stage an attachment for preview instead of sending it right away.
  const stageFile = (file) => {
    if (!file || !activeId) return;
    setPending((p) => {
      if (p) URL.revokeObjectURL(p.url);
      return { file, kind: kindFor(file.type), url: URL.createObjectURL(file),
               name: file.name, size: file.size };
    });
  };

  const cancelPending = () => {
    setPending((p) => {
      if (p) URL.revokeObjectURL(p.url);
      return null;
    });
  };

  const sendPending = async () => {
    if (!pending || sending) return;
    setSending(true);
    setError(null);
    try {
      const stored = await api.uploadChatMedia(pending.file);
      const caption = draft.trim() || null;
      await deliver({
        kind: stored.kind, media_url: stored.url, media_type: stored.media_type,
        // FILE bubbles show body as the link label; media kinds show it as a caption.
        body: stored.kind === "FILE" ? (caption || pending.name) : caption,
        meta: { filename: pending.name, size: pending.size },
      });
      cancelPending();
      setDraft("");
    } catch (e) { setError(e.message); }
    finally { setSending(false); }
  };

  const send = () => {
    if (pending) return sendPending();
    const body = draft.trim();
    if (!body || !activeId) return;
    setDraft("");
    deliver({ body });
  };

  const toggleVoiceNote = async () => {
    if (recording) {
      recorderRef.current?.stop();
      setRecording(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        // Stage it — the user listens, captions, then sends (or cancels).
        stageFile(new File([blob], "voice-note.webm", { type: blob.type }));
      };
      rec.start();
      recorderRef.current = rec;
      setRecording(true);
    } catch (e) {
      setError("Microphone unavailable.");
    }
  };

  const startDm = async (userId) => {
    try {
      const room = await api.createChatRoom({ user_id: userId });
      setShowNew(false);
      await loadRooms();
      getSocket()?.emit("chat:join-room", { room_id: room.id });
      openRoom(room.id);
    } catch (e) { setError(e.message); }
  };

  const createGroup = async (e) => {
    e.preventDefault();
    try {
      const room = await api.createChatRoom({
        name: groupForm.name, member_ids: groupForm.ids });
      setShowNew(false);
      setGroupForm({ name: "", ids: [] });
      await loadRooms();
      getSocket()?.emit("chat:join-room", { room_id: room.id });
      openRoom(room.id);
    } catch (e2) { setError(e2.message); }
  };

  const active = rooms.find((r) => r.id === activeId);
  const inCallHere = call && call.roomId === activeId;

  return (
    <>
      <div className="d-flex justify-content-between align-items-start" style={{ marginBottom: "1rem" }}>
        <div>
          <h3 style={{ margin: 0 }}>Team Chat</h3>
          <p className="muted" style={{ margin: ".15rem 0 0", fontSize: ".85rem" }}>
            Direct messages, group rooms, voice notes and calls — inside the platform.
          </p>
        </div>
        <button className="btn btn-co btn-sm" onClick={() => setShowNew(!showNew)}>
          <i className="fa-solid fa-pen-to-square" /> New chat
        </button>
      </div>

      {preview && (
        <FilePreview {...preview} onClose={() => setPreview(null)} />
      )}

      {error && <div className="alert alert-danger py-2">{error}</div>}

      {incoming && (
        <div className="ch-ringing">
          <i className={`fa-solid ${incoming.media === "video" ? "fa-video" : "fa-phone"} fa-beat`} />
          <span><b>{incoming.from_name}</b> is calling…</span>
          <button className="btn btn-sm btn-success" onClick={acceptCall}>Join</button>
          <button className="btn btn-sm btn-outline-secondary" onClick={() => setIncoming(null)}>Ignore</button>
        </div>
      )}

      {showNew && (
        <div className="co-card" style={{ marginBottom: "1rem" }}>
          <div className="row g-3">
            <div className="col-md-5">
              <div className="section-title">Direct message</div>
              <input className="form-control form-control-sm"
                placeholder="Search colleagues or customers…"
                value={dirSearch} onChange={(e) => setDirSearch(e.target.value)}
                style={{ marginBottom: ".4rem" }} />
              {colleagues
                .filter((c) => {
                  const q = dirSearch.trim().toLowerCase();
                  if (!q) return true;
                  return [c.full_name, c.email, c.customer_name]
                    .some((v) => (v || "").toLowerCase().includes(q));
                })
                .map((c) => (
                <div className="work-row" key={c.id} style={{ cursor: "pointer" }}
                  onClick={() => startDm(c.id)}>
                  <span className="co-avatar" style={{ width: 26, height: 26, fontSize: ".62rem" }}>
                    {(c.full_name || c.email)[0].toUpperCase()}
                  </span>
                  <div className="grow">
                    <div className="title">
                      {c.full_name || c.email}
                      {c.is_portal_user && <span className="chip INFO" style={{ marginLeft: ".35rem" }}>customer</span>}
                    </div>
                    <div className="meta">
                      {c.customer_name || c.role.replace(/_/g, " ")}
                    </div>
                  </div>
                  <i className="fa-solid fa-comment muted" />
                </div>
              ))}
              {colleagues.length === 0 && (
                <div className="empty" style={{ fontSize: ".82rem" }}>
                  No contacts available yet.
                </div>
              )}
            </div>
            {/* Customers only get direct messages with their reference. */}
            <div className="col-md-7" hidden={store.user?.is_portal_user}>
              <div className="section-title">New group</div>
              <form onSubmit={createGroup}>
                <input className="form-control form-control-sm" placeholder="Group name…"
                  value={groupForm.name}
                  onChange={(e) => setGroupForm({ ...groupForm, name: e.target.value })} />
                <div style={{ display: "flex", flexWrap: "wrap", gap: ".35rem", margin: ".6rem 0" }}>
                  {colleagues.map((c) => {
                    const on = groupForm.ids.includes(c.id);
                    return (
                      <button type="button" key={c.id}
                        className={"kf-chip" + (on ? " on" : "")}
                        onClick={() => setGroupForm({
                          ...groupForm,
                          ids: on ? groupForm.ids.filter((i) => i !== c.id)
                            : [...groupForm.ids, c.id],
                        })}>
                        {c.full_name || c.email}
                      </button>
                    );
                  })}
                </div>
                <button className="btn btn-co btn-sm" disabled={!groupForm.name.trim()}>
                  Create group
                </button>
              </form>
            </div>
          </div>
        </div>
      )}

      <div className="ch-layout">
        {/* Room list */}
        <aside className="co-card ch-rooms">
          <input className="form-control form-control-sm ch-search"
            placeholder="Search conversations…" value={roomSearch}
            onChange={(e) => setRoomSearch(e.target.value)} />
          {rooms.length === 0 && <div className="empty">No conversations yet.</div>}
          {rooms
            .filter((r) => {
              const q = roomSearch.trim().toLowerCase();
              if (!q) return true;
              return (r.display_name || "").toLowerCase().includes(q)
                || (r.members || []).some((m) =>
                  (m.full_name || m.email || "").toLowerCase().includes(q));
            })
            .map((r) => (
            <button key={r.id}
              className={"ch-room" + (r.id === activeId ? " active" : "")}
              onClick={() => openRoom(r.id)}>
              <span className="co-avatar" style={{ width: 34, height: 34, fontSize: ".72rem" }}>
                {r.is_group ? <i className="fa-solid fa-users" /> : r.display_name[0]?.toUpperCase()}
              </span>
              <span className="ch-room-main">
                <span className="ch-room-name">{r.display_name}</span>
                <span className="ch-room-last">
                  {r.last_message
                    ? (r.last_message.kind === "TEXT" || r.last_message.kind === "SYSTEM"
                      ? (r.last_message.body || "").slice(0, 34)
                      : `· ${r.last_message.kind.toLowerCase()}`)
                    : "No messages yet"}
                </span>
              </span>
              {r.unread > 0 && <span className="ch-unread">{r.unread}</span>}
            </button>
          ))}
        </aside>

        {/* Thread */}
        <section className="co-card ch-thread">
          {!active && <div className="empty" style={{ margin: "auto" }}>
            Pick a conversation or start a new one.</div>}

          {active && (
            <>
              <header className="ch-head">
                <b>{active.display_name}</b>
                <span className="muted" style={{ fontSize: ".78rem" }}>
                  {active.is_customer_room
                    ? (store.user?.is_portal_user
                        ? "Your compliance team"
                        : `Customer conversation · ${active.members.length} on the file`)
                    : active.is_group
                      ? `${active.members.length} members`
                      : "Direct message"}
                </span>
                <span style={{ flex: 1 }} />
                {!call && (
                  <>
                    <button className="btn btn-sm btn-outline-secondary" title="Audio call"
                      onClick={() => startCall("audio")}>
                      <i className="fa-solid fa-phone" />
                    </button>
                    <button className="btn btn-sm btn-co" title="Video call"
                      onClick={() => startCall("video")}>
                      <i className="fa-solid fa-video" /> Meet
                    </button>
                  </>
                )}
              </header>

              {inCallHere && (
                <div className="ch-call">
                  <div className="ch-call-grid">
                    <div className="ch-tile">
                      <video ref={localVideoRef} autoPlay muted playsInline />
                      <span className="ch-tile-name">
                        You{!micOn && hasMedia ? " · muted" : ""}
                        {hasMedia ? "" : " · viewer"}
                      </span>
                      {hasMedia && !camOn && (
                        <span className="ch-tile-off"><i className="fa-solid fa-video-slash" /></span>
                      )}
                    </div>
                    {Object.entries(remoteStreams).map(([uid, stream]) => (
                      <RemoteTile key={uid} stream={stream}
                        name={active.members.find((m) => m.user_id === Number(uid))?.full_name || "Peer"} />
                    ))}
                  </div>
                  <div className="ch-call-controls">
                    <button
                      className={`btn btn-sm ${micOn ? "btn-outline-light" : "btn-danger"}`}
                      title={hasMedia ? (micOn ? "Mute microphone" : "Unmute microphone")
                        : "No microphone (viewer mode)"}
                      disabled={!hasMedia} onClick={toggleMic}>
                      <i className={`fa-solid ${micOn ? "fa-microphone" : "fa-microphone-slash"}`} />
                      {!micOn && " Muted"}
                    </button>
                    <button
                      className={`btn btn-sm ${camOn ? "btn-outline-light" : "btn-danger"}`}
                      title={call.media !== "video" ? "Audio call (no camera)"
                        : hasMedia ? (camOn ? "Turn camera off" : "Turn camera on")
                          : "No camera (viewer mode)"}
                      disabled={!hasMedia || call.media !== "video"} onClick={toggleCam}>
                      <i className={`fa-solid ${camOn ? "fa-video" : "fa-video-slash"}`} />
                      {!camOn && call.media === "video" && hasMedia && " Off"}
                    </button>
                    <button className="btn btn-sm btn-danger" onClick={() => hangup()}>
                      <i className="fa-solid fa-phone-slash" /> Leave
                    </button>
                  </div>
                </div>
              )}

              <div className="ch-messages" ref={scrollRef}>
                {messages.map((m) => (
                  <Bubble key={m.id} m={m} mine={m.sender_id === myId}
                    asOrganization={!!store.user?.is_portal_user}
                    onPreview={setPreview} />
                ))}
                {typing && <div className="ch-typing">{typing} is typing…</div>}
              </div>

              <div className="ch-composer-wrap">
                {recording && (
                  <div className="ch-pending ch-recording">
                    <span className="ch-rec-dot" />
                    <span>Recording voice note… press <i className="fa-solid fa-stop" /> to finish.</span>
                  </div>
                )}

                {pending && (
                  <div className="ch-pending">
                    <div className="ch-pending-preview">
                      {pending.kind === "AUDIO" && (
                        <audio controls src={pending.url} className="ch-audio" />
                      )}
                      {pending.kind === "IMAGE" && (
                        <img src={pending.url} alt="" className="ch-pending-img" />
                      )}
                      {pending.kind === "VIDEO" && (
                        <video controls src={pending.url} className="ch-pending-video" />
                      )}
                      {pending.kind === "FILE" && (
                        <span className="ch-pending-file">
                          <i className="fa-solid fa-paperclip" /> {pending.name}
                        </span>
                      )}
                      <span className="ch-pending-meta">
                        {pending.kind === "AUDIO" && pending.name === "voice-note.webm"
                          ? "Voice note" : pending.name} · {fmtSize(pending.size)}
                      </span>
                    </div>
                    <span className="ch-pending-hint">
                      Add a caption below, then send — or cancel.
                    </span>
                    <button className="btn btn-sm btn-outline-danger" title="Cancel attachment"
                      onClick={cancelPending} disabled={sending}>
                      <i className="fa-solid fa-xmark" />
                    </button>
                  </div>
                )}

                <div className="ch-composer">
                  <label className={`btn btn-sm btn-outline-secondary ${pending || recording ? "disabled" : ""}`}
                    title="Attach file">
                    <i className="fa-solid fa-paperclip" />
                    <input type="file" hidden disabled={!!pending || recording}
                      onChange={(e) => { stageFile(e.target.files[0]); e.target.value = ""; }} />
                  </label>
                  <button className={`btn btn-sm ${recording ? "btn-danger" : "btn-outline-secondary"}`}
                    title={recording ? "Stop recording" : "Record voice note"}
                    disabled={!!pending} onClick={toggleVoiceNote}>
                    <i className={`fa-solid ${recording ? "fa-stop" : "fa-microphone"}`} />
                  </button>
                  <input className="form-control"
                    placeholder={pending ? "Add a caption (optional)…" : "Message…"}
                    value={draft}
                    onChange={(e) => {
                      setDraft(e.target.value);
                      getSocket()?.emit("chat:typing", { room_id: activeId });
                    }}
                    onKeyDown={(e) => e.key === "Enter" && send()} />
                  <button className="btn btn-co" onClick={send}
                    disabled={sending || (!pending && !draft.trim())}>
                    <i className={`fa-solid ${sending ? "fa-spinner fa-spin" : "fa-paper-plane"}`} />
                  </button>
                </div>
              </div>
            </>
          )}
        </section>
      </div>
    </>
  );
};

const RemoteTile = ({ stream, name }) => {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.srcObject = stream;
  }, [stream]);
  return (
    <div className="ch-tile">
      <video ref={ref} autoPlay playsInline />
      <span className="ch-tile-name">{name}</span>
    </div>
  );
};
