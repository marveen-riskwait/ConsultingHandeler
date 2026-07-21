// Singleton Socket.IO connection for chat + call signalling.
// Connects lazily with the JWT; reconnects transparently. Components subscribe
// via on()/off() and must clean up on unmount.
import { io } from "socket.io-client";

const BASE = (import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "");

let socket = null;

export function getSocket() {
  const token = localStorage.getItem("token");
  if (!token) return null;
  if (socket && socket.connected) return socket;
  if (socket) return socket; // connecting/reconnecting
  socket = io(BASE || window.location.origin, {
    auth: { token },
    // Polling first, then upgrade: connects reliably everywhere (Codespace
    // port-forwarding, dev servers where the WS upgrade 500s) and Engine.IO
    // switches to a real WebSocket only when the upgrade actually works.
    transports: ["polling", "websocket"],
  });
  return socket;
}

export function resetSocket() {
  if (socket) {
    socket.disconnect();
    socket = null;
  }
}
