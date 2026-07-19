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
  setProviderCredential: (id, payload) => request(`/providers/${id}/credentials`, { method: "POST", body: payload }),
  providerHealth: (id) => request(`/providers/${id}/health`, { method: "POST" }),
  webhookEvents: () => request("/webhook-events"),

  invitations: () => request("/invitations"),
  createInvitation: (payload) => request("/invitations", { method: "POST", body: payload }),
  revokeInvitation: (id) => request(`/invitations/${id}/revoke`, { method: "POST" }),
  acceptInvitation: (payload) => request("/auth/accept-invitation", { method: "POST", body: payload }),
};
