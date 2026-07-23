// Thin fetch wrapper around the Compliance OS REST API.
// Same-origin: the Vite dev proxy forwards /api and /socket.io to Flask,
// and in production Flask serves this bundle itself. No base URL needed.
const BASE = "";

// The JWT now lives in an httpOnly cookie the browser sends automatically, so
// there is no token for JavaScript (or an XSS) to read. For state-changing
// requests we echo the readable CSRF cookie in a header — this is what proves
// the request came from our own page and not another site riding the cookie.
function readCookie(name) {
  const m = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
  return m ? decodeURIComponent(m[2]) : null;
}

function buildHeaders(method) {
  const headers = { "Content-Type": "application/json" };
  if (method && method.toUpperCase() !== "GET") {
    const csrf = readCookie("csrf_access_token");
    if (csrf) headers["X-CSRF-TOKEN"] = csrf;
  }
  return headers;
}

let refreshing = null;   // de-dupe concurrent refreshes

// During the 2FA step there is no session cookie yet; the short-lived ticket
// authorizes the call as a bearer token.
async function ticketPost(path, ticket, body) {
  const res = await fetch(`${BASE}/api${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json",
               Authorization: `Bearer ${ticket}` },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || `Request failed (${res.status})`);
  return data;
}

async function tryRefresh() {
  if (!refreshing) {
    refreshing = fetch(`${BASE}/api/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: (() => {
        const c = readCookie("csrf_refresh_token");
        return c ? { "X-CSRF-TOKEN": c } : {};
      })(),
    }).then((r) => r.ok).catch(() => false).finally(() => { refreshing = null; });
  }
  return refreshing;
}

async function rawFetch(path, method, body) {
  return fetch(`${BASE}/api${path}`, {
    method,
    credentials: "include",              // send the httpOnly auth cookies
    headers: buildHeaders(method),
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function request(path, { method = "GET", body } = {}) {
  let res = await rawFetch(path, method, body);

  // A stale 30-minute access token: silently refresh once and retry, so the
  // user never sees a session drop mid-work.
  if (res.status === 401 && path !== "/auth/refresh" && path !== "/auth/login") {
    if (await tryRefresh()) res = await rawFetch(path, method, body);
  }
  if (res.status === 401) localStorage.removeItem("user");

  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  if (!res.ok) {
    throw new Error(data.message || `Request failed (${res.status})`);
  }
  return data;
}

export const api = {
  // auth
  login: (email, password) => request("/auth/login", { method: "POST", body: { email, password } }),
  register: (payload) => request("/auth/register", { method: "POST", body: payload }),
  me: () => request("/auth/me"),
  logout: () => request("/auth/logout", { method: "POST" }),
  verifyEmail: (token) => request("/auth/verify-email", { method: "POST", body: { token } }),
  resendVerification: () => request("/auth/resend-verification", { method: "POST" }),
  forgotPassword: (email) => request("/auth/forgot-password", { method: "POST", body: { email } }),
  resetPassword: (token, password) => request("/auth/reset-password", { method: "POST", body: { token, password } }),
  // MFA step-up during login uses the pending ticket as a bearer token.
  mfaVerify: (ticket, code) => ticketPost("/auth/mfa", ticket, { code }),
  mfaEnroll: (ticket) => ticketPost("/auth/mfa/enroll", ticket, {}),
  mfaConfirm: (ticket, code) => ticketPost("/auth/mfa/confirm", ticket, { code }),
  mfaEnrollSession: () => request("/auth/mfa/enroll", { method: "POST" }),
  mfaConfirmSession: (code) => request("/auth/mfa/confirm", { method: "POST", body: { code } }),
  mfaDisable: () => request("/auth/mfa", { method: "DELETE" }),
  updateProfile: (fields) => request("/profile", { method: "PATCH", body: fields }),
  changePassword: (current_password, new_password) =>
    request("/profile/password", { method: "POST", body: { current_password, new_password } }),
  removeAvatar: () => request("/profile/avatar", { method: "DELETE" }),
  uploadAvatar: async (file) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/api/profile/avatar`, {
      method: "POST", credentials: "include",
      headers: (() => { const c = readCookie("csrf_access_token"); return c ? { "X-CSRF-TOKEN": c } : {}; })(),
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.message || `Upload failed (${res.status})`);
    return data;
  },

  // customers
  customers: (archived) => request(`/customers${archived ? "?archived=1" : ""}`),
  nameSuggestions: (q) => request(`/name-suggestions?q=${encodeURIComponent(q)}`),
  createCustomer: (payload) => request("/customers", { method: "POST", body: payload }),
  customer: (id) => request(`/customers/${id}`),
  screen: (id) => request(`/customers/${id}/screen`, { method: "POST" }),
  addDocument: (id, payload) => request(`/customers/${id}/documents`, { method: "POST", body: payload }),
  uploadDocument: async (id, docType, file) => {
    const form = new FormData();
    form.append("file", file);
    form.append("doc_type", docType);
    const res = await fetch(`${BASE}/api/customers/${id}/documents`, {
      method: "POST",
      credentials: "include",
      headers: (() => {
        const c = readCookie("csrf_access_token");
        return c ? { "X-CSRF-TOKEN": c } : {};
      })(),
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.message || `Upload failed (${res.status})`);
    return data;
  },
  deleteDocument: (id, docId) =>
    request(`/customers/${id}/documents/${docId}`, { method: "DELETE" }),
  timeline: (id) => request(`/customers/${id}/timeline`),
  ownership: (id) => request(`/customers/${id}/ownership`),
  addOwnership: (id, payload) => request(`/customers/${id}/ownership`, { method: "POST", body: payload }),
  addresses: (id) => request(`/customers/${id}/addresses`),
  addAddress: (id, payload) => request(`/customers/${id}/addresses`, { method: "POST", body: payload }),
  fields: (id) => request(`/customers/${id}/fields`),
  setField: (id, payload) => request(`/customers/${id}/fields`, { method: "POST", body: payload }),
  verifyField: (id, fid) => request(`/customers/${id}/fields/${fid}/verify`, { method: "POST" }),
  requirements: (id) => request(`/customers/${id}/requirements`),
  requestInfo: (id) => request(`/customers/${id}/request-info`, { method: "POST" }),
  screening: (id) => request(`/customers/${id}/screening`),
  reviewMatch: (matchId, decision, reason) =>
    request(`/screening/matches/${matchId}/review`, { method: "POST", body: { decision, reason } }),

  // workspace / work
  workspace: () => request("/workspace"),
  myWork: () => request("/tasks/my-work"),
  completeTask: (id) => request(`/tasks/${id}/complete`, { method: "POST" }),

  // cases
  cases: (status) => request(`/cases${status ? `?status=${status}` : ""}`),
  case: (id) => request(`/cases/${id}`),
  decideCase: (id, decision, reason) =>
    request(`/cases/${id}/decision`, { method: "POST", body: { decision, reason } }),
  startWorkflow: (caseId) => request(`/cases/${caseId}/workflow/start`, { method: "POST" }),
  completeStep: (instanceId, note) => request(`/workflow-instances/${instanceId}/complete-step`, { method: "POST", body: { note } }),
  approveStep: (instanceId, decision, reason) => request(`/workflow-instances/${instanceId}/approve`, { method: "POST", body: { decision, reason } }),

  // notifications / rules / audit
  notifications: () => request("/notifications"),
  readNotification: (id) => request(`/notifications/${id}/read`, { method: "POST" }),
  rules: () => request("/rules"),
  audit: (params = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString();
    return request(`/audit${q ? `?${q}` : ""}`);
  },

  // alerts & reviews
  alerts: (status) => request(`/alerts${status ? `?status=${status}` : ""}`),
  assignAlert: (id, payload) => request(`/alerts/${id}/assign`, { method: "POST", body: payload || {} }),
  resolveAlert: (id, payload) => request(`/alerts/${id}/resolve`, { method: "POST", body: payload }),
  reviews: (id) => request(`/customers/${id}/reviews`),
  createReview: (id, payload) => request(`/customers/${id}/reviews`, { method: "POST", body: payload }),
  startReview: (rid) => request(`/reviews/${rid}/start`, { method: "POST" }),
  completeReview: (rid, payload) => request(`/reviews/${rid}/complete`, { method: "POST", body: payload }),
  runMonitoring: () => request("/monitoring/run", { method: "POST" }),

  // regulatory intelligence
  regulatory: () => request("/regulatory"),
  regulatorySources: () => request("/regulatory/sources"),
  createRegulatoryChange: (payload) => request("/regulatory/changes", { method: "POST", body: payload }),
  assessRegulatoryChange: (id, notes) => request(`/regulatory/changes/${id}/assess`, { method: "POST", body: { notes } }),

  // administration
  organization: () => request("/organization"),
  updateOrganization: (payload) => request("/organization", { method: "PATCH", body: payload }),
  users: () => request("/users"),
  updateUser: (id, payload) => request(`/users/${id}`, { method: "PATCH", body: payload }),
  teams: () => request("/teams"),
  createTeam: (payload) => request("/teams", { method: "POST", body: payload }),
  addTeamMember: (teamId, payload) => request(`/teams/${teamId}/members`, { method: "POST", body: payload }),
  departments: () => request("/departments"),
  createDepartment: (payload) => request("/departments", { method: "POST", body: payload }),
  roles: () => request("/roles"),
  permissionsCatalog: () => request("/permissions"),
  riskMethodologies: () => request("/risk/methodologies"),
  countryLists: () => request("/risk/country-lists"),
  syncCountryLists: () => request("/risk/country-lists/sync", { method: "POST" }),
  // management
  managementDashboard: () => request("/management/dashboard"),
  managementWorkload: () => request("/management/workload"),
  managementQueues: () => request("/management/queues"),
  managementSla: () => request("/management/sla"),
  assignCase: (id, payload) => request(`/cases/${id}/assign`, { method: "POST", body: payload }),
  bulkAssign: (strategy) => request("/management/queues/bulk-assign", { method: "POST", body: { strategy } }),

  providers: () => request("/providers"),
  createProvider: (payload) => request("/providers", { method: "POST", body: payload }),
  updateProvider: (id, payload) => request(`/providers/${id}`, { method: "PATCH", body: payload }),
  assignableUsers: () => request("/users/assignable"),
  setProviderCredential: (id, payload) => request(`/providers/${id}/credentials`, { method: "POST", body: payload }),
  deleteProviderCredential: (id, keyName) => request(`/providers/${id}/credentials`, { method: "DELETE", body: { key_name: keyName } }),
  providerHealth: (id) => request(`/providers/${id}/health`, { method: "POST" }),
  webhookEvents: () => request("/webhook-events"),

  invitations: () => request("/invitations"),
  createInvitation: (payload) => request("/invitations", { method: "POST", body: payload }),
  revokeInvitation: (id) => request(`/invitations/${id}/revoke`, { method: "POST" }),
  acceptInvitation: (payload) => request("/auth/accept-invitation", { method: "POST", body: payload }),

  // Customer portal (client-facing surface; see api/portal.py)
  portalMe: () => request("/portal/me"),
  portalForm: () => request("/portal/kyc-form"),
  portalSaveForm: (fields) =>
    request("/portal/kyc-form", { method: "POST", body: { fields } }),
  portalSubmit: () => request("/portal/kyc-form/submit", { method: "POST" }),
  portalReopen: () => request("/portal/kyc-form/reopen", { method: "POST" }),
  portalDocuments: () => request("/portal/documents"),
  portalDeleteDocument: (id) =>
    request(`/portal/documents/${id}`, { method: "DELETE" }),
  portalUploadDocument: async (docType, description, file) => {
    const form = new FormData();
    form.append("file", file);
    form.append("doc_type", docType);
    if (description) form.append("description", description);
    const res = await fetch(`${BASE}/api/portal/documents`, {
      method: "POST",
      credentials: "include",
      headers: (() => {
        const c = readCookie("csrf_access_token");
        return c ? { "X-CSRF-TOKEN": c } : {};
      })(),
      body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.message || `Upload failed (${res.status})`);
    return data;
  },
  portalAssistant: () => request("/portal/assistant"),
  portalAsk: (message) =>
    request("/portal/assistant", { method: "POST", body: { message } }),
  rejectionReasons: () => request("/portal/rejection-reasons"),
  reviewDocument: (cid, did, payload) =>
    request(`/customers/${cid}/documents/${did}/review`, { method: "POST", body: payload }),

  // Team chat
  chatUsers: () => request("/chat/users"),
  chatRooms: () => request("/chat/rooms"),
  openCustomerRoom: (customerId) =>
    request(`/customers/${customerId}/chat-room`, { method: "POST" }),
  createChatRoom: (payload) => request("/chat/rooms", { method: "POST", body: payload }),
  addChatMember: (roomId, userId) =>
    request(`/chat/rooms/${roomId}/members`, { method: "POST", body: { user_id: userId } }),
  chatMessages: (roomId, beforeId) =>
    request(`/chat/rooms/${roomId}/messages${beforeId ? `?before_id=${beforeId}` : ""}`),
  sendChatMessage: (roomId, payload) =>
    request(`/chat/rooms/${roomId}/messages`, { method: "POST", body: payload }),
  markChatRead: (roomId) => request(`/chat/rooms/${roomId}/read`, { method: "POST" }),
  uploadChatMedia: async (file) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/api/chat/upload`, {
      method: "POST",
      credentials: "include",
      headers: (() => {
        const c = readCookie("csrf_access_token");
        return c ? { "X-CSRF-TOKEN": c } : {};
      })(),
      body: form,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || `Upload failed (${res.status})`);
    return data;
  },

  // Permission management (clickable matrix + special authorizations)
  toggleRolePermission: (roleId, code, enabled) =>
    request(`/roles/${roleId}/permissions`, { method: "POST", body: { code, enabled } }),
  toggleUserPermission: (userId, code, enabled) =>
    request(`/users/${userId}/permissions`, { method: "POST", body: { code, enabled } }),

  // KYC intake form
  kycForm: (id) => request(`/customers/${id}/kyc-form`),
  saveKycForm: (id, fields) =>
    request(`/customers/${id}/kyc-form`, { method: "POST", body: { fields } }),
  submitKycForm: (id) =>
    request(`/customers/${id}/kyc-form/submit`, { method: "POST" }),

  // Public watchlists (OFAC / UN / EU) + Companies House
  watchlists: () => request("/watchlists"),
  watchlistSearch: (q) => request(`/watchlists/search?q=${encodeURIComponent(q)}`),
  ingestWatchlists: (source) =>
    request("/watchlists/ingest", { method: "POST", body: { source: source || "ALL" } }),
  screenWallet: (address) =>
    request(`/watchlists/wallet?address=${encodeURIComponent(address)}`),
  kybLookup: (id) => request(`/customers/${id}/kyb-lookup`, { method: "POST" }),
  enrich: (id) => request(`/customers/${id}/enrich`, { method: "POST" }),
  portalAccess: (id) => request(`/customers/${id}/portal-access`),
  invitePortal: (id, email) =>
    request(`/customers/${id}/portal-access`, { method: "POST", body: { email } }),
  revokePortalInvite: (id, iid) =>
    request(`/customers/${id}/portal-access/${iid}`, { method: "DELETE" }),
  deletionCheck: (id) => request(`/customers/${id}/deletion-check`),
  deleteCustomer: (id, payload) =>
    request(`/customers/${id}`, { method: "DELETE", body: payload }),
  archiveCustomer: (id, reason) =>
    request(`/customers/${id}/archive`, { method: "POST", body: { reason } }),
  restoreCustomer: (id, reason) =>
    request(`/customers/${id}/restore`, { method: "POST", body: { reason } }),

  // Compliance Copilot (AI assistant)
  assistantMeta: () => request("/assistant/meta"),
  assistantCheck: () => request("/assistant/check", { method: "POST" }),
  conversations: () => request("/assistant/conversations"),
  createConversation: (payload) => request("/assistant/conversations", { method: "POST", body: payload || {} }),
  conversation: (id) => request(`/assistant/conversations/${id}`),
  sendAssistantMessage: (id, content) =>
    request(`/assistant/conversations/${id}/messages`, { method: "POST", body: { content } }),
};
