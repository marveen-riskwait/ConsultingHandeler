// Thin fetch wrapper around the Compliance OS REST API.
const BASE = (import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "");

function authHeaders() {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request(path, { method = "GET", body } = {}) {
  const res = await fetch(`${BASE}/api${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401) {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
  }

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

  // customers
  customers: () => request("/customers"),
  createCustomer: (payload) => request("/customers", { method: "POST", body: payload }),
  customer: (id) => request(`/customers/${id}`),
  screen: (id) => request(`/customers/${id}/screen`, { method: "POST" }),
  addDocument: (id, payload) => request(`/customers/${id}/documents`, { method: "POST", body: payload }),
  timeline: (id) => request(`/customers/${id}/timeline`),
  ownership: (id) => request(`/customers/${id}/ownership`),
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

  // notifications / rules / audit
  notifications: () => request("/notifications"),
  readNotification: (id) => request(`/notifications/${id}/read`, { method: "POST" }),
  rules: () => request("/rules"),

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
  invitations: () => request("/invitations"),
  createInvitation: (payload) => request("/invitations", { method: "POST", body: payload }),
  revokeInvitation: (id) => request(`/invitations/${id}/revoke`, { method: "POST" }),
  acceptInvitation: (payload) => request("/auth/accept-invitation", { method: "POST", body: payload }),
};
