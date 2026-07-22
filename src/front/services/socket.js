// Singleton Socket.IO connection for chat + call signalling.
// Connects lazily with the JWT; reconnects transparently. Components subscribe
// via on()/off() and must clean up on unmount.
import { io } from "socket.io-client";

// Same-origin: the Vite dev proxy forwards /api and /socket.io to Flask,
// and in production Flask serves this bundle itself. No base URL needed.
const BASE = "";

let socket = null;

export function getSocket() {
  // Only connect for an authenticated session; the httpOnly auth cookie rides
  // the same-origin handshake automatically, so there is no token to pass.
  if (localStorage.getItem("authed") !== "1") return null;
  if (socket && socket.connected) return socket;
  if (socket) return socket; // connecting/reconnecting
  socket = io(BASE || window.location.origin, {
    withCredentials: true,
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
