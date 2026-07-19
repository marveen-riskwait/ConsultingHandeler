import { useEffect, useState, useCallback } from "react";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";
import { can } from "../permissions/can";

const ROLE_OPTIONS = [
  "KYC_ANALYST", "SENIOR_ANALYST", "COMPLIANCE_OFFICER", "COMPLIANCE_MANAGER",
  "MLRO", "AUDITOR", "REGULATORY_MANAGER", "ORGANIZATION_ADMIN", "CUSTOMER_USER",
];

// ---------------------------------------------------------------- Users tab
const UsersTab = ({ me }) => {
  const [users, setUsers] = useState([]);
  const [invitations, setInvitations] = useState([]);
  const [teams, setTeams] = useState([]);
  const [error, setError] = useState(null);
  const [inviteForm, setInviteForm] = useState({ email: "", proposed_role: "KYC_ANALYST", proposed_team_id: "" });
  const [lastToken, setLastToken] = useState(null);

  const load = useCallback(() => {
    api.users().then(setUsers).catch((e) => setError(e.message));
    api.invitations().then(setInvitations).catch(() => {});
    api.teams().then(setTeams).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const invite = async (e) => {
    e.preventDefault();
    setError(null);
    try {
      const inv = await api.createInvitation({
        ...inviteForm,
        proposed_team_id: inviteForm.proposed_team_id || null,
      });
      setLastToken(inv.token);
      setInviteForm({ email: "", proposed_role: "KYC_ANALYST", proposed_team_id: "" });
      load();
    } catch (err) { setError(err.message); }
  };

  const changeRole = async (u, role) => {
    setError(null);
    try { await api.updateUser(u.id, { role }); load(); }
    catch (err) { setError(err.message); }
  };

  const toggleActive = async (u, isActive) => {
    setError(null);
    try { await api.updateUser(u.id, { is_active: isActive }); load(); }
    catch (err) { setError(err.message); }
  };

  const canUpdate = can(me, "user.update");
  const canInvite = can(me, "user.create");

  return (
    <>
      {error && <div className="alert alert-danger py-2">{error}</div>}

      {canInvite && (
        <div className="co-card" style={{ marginBottom: "1rem" }}>
          <div className="section-title">Invite a user</div>
          <form className="row g-2 align-items-end" onSubmit={invite}>
            <div className="col-md-4">
              <label className="form-label">Email</label>
              <input type="email" className="form-control" required value={inviteForm.email}
                onChange={(e) => setInviteForm({ ...inviteForm, email: e.target.value })} />
            </div>
            <div className="col-md-3">
              <label className="form-label">Role</label>
              <select className="form-select" value={inviteForm.proposed_role}
                onChange={(e) => setInviteForm({ ...inviteForm, proposed_role: e.target.value })}>
                {ROLE_OPTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div className="col-md-3">
              <label className="form-label">Team (optional)</label>
              <select className="form-select" value={inviteForm.proposed_team_id}
                onChange={(e) => setInviteForm({ ...inviteForm, proposed_team_id: e.target.value })}>
                <option value="">—</option>
                {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>
            <div className="col-md-2">
              <button className="btn btn-co w-100">Invite</button>
            </div>
          </form>
          {lastToken && (
            <div className="alert alert-success py-2 mt-3 mb-0" style={{ fontSize: ".85rem" }}>
              Invitation created. Share this link:{" "}
              <code>{window.location.origin}/?invite={lastToken}</code>
            </div>
          )}
        </div>
      )}

      <div className="co-card">
        <div className="section-title">Users ({users.length})</div>
        {users.map((u) => (
          <div className="work-row" key={u.id}>
            <span className={`dotsev ${u.is_active === false ? "LOW" : "INFO"}`} />
            <div className="grow">
              <div className="title">{u.full_name} <span className="muted" style={{ fontWeight: 400 }}>· {u.email}</span></div>
              <div className="meta">{(u.roles || [u.role]).join(", ")}</div>
            </div>
            {canUpdate && u.id !== me.id ? (
              <>
                <select className="form-select form-select-sm" style={{ width: 190 }}
                  value={u.role} onChange={(e) => changeRole(u, e.target.value)}>
                  {ROLE_OPTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <button className={`btn btn-sm ${u.is_active === false ? "btn-outline-success" : "btn-outline-danger"}`}
                  onClick={() => toggleActive(u, u.is_active === false)}>
                  {u.is_active === false ? "Enable" : "Disable"}
                </button>
              </>
            ) : (
              <span className="chip INFO">{u.role}</span>
            )}
          </div>
        ))}
      </div>

      <div className="co-card">
        <div className="section-title">Invitations</div>
        {invitations.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No invitations.</div>}
        {invitations.map((i) => (
          <div className="work-row" key={i.id}>
            <span className={`dotsev ${i.status === "PENDING" ? "MEDIUM" : "INFO"}`} />
            <div className="grow">
              <div className="title">{i.email}</div>
              <div className="meta">{i.proposed_role} · expires {i.expires_at ? new Date(i.expires_at).toLocaleDateString() : "—"}</div>
            </div>
            <span className={`chip ${i.status === "PENDING" ? "MEDIUM" : i.status === "ACCEPTED" ? "LOW" : "INFO"}`}>{i.status}</span>
            {i.status === "PENDING" && canInvite && (
              <button className="btn btn-sm btn-outline-secondary"
                onClick={() => api.revokeInvitation(i.id).then(load)}>Revoke</button>
            )}
          </div>
        ))}
      </div>
    </>
  );
};

// ---------------------------------------------------------------- Teams tab
const TeamsTab = ({ me }) => {
  const [teams, setTeams] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [users, setUsers] = useState([]);
  const [error, setError] = useState(null);
  const [teamForm, setTeamForm] = useState({ name: "", department_id: "" });
  const [deptName, setDeptName] = useState("");

  const load = useCallback(() => {
    api.teams().then(setTeams).catch((e) => setError(e.message));
    api.departments().then(setDepartments).catch(() => {});
    api.users().then(setUsers).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const createTeam = async (e) => {
    e.preventDefault();
    try {
      await api.createTeam({ name: teamForm.name, department_id: teamForm.department_id || null });
      setTeamForm({ name: "", department_id: "" });
      load();
    } catch (err) { setError(err.message); }
  };

  const createDept = async (e) => {
    e.preventDefault();
    try { await api.createDepartment({ name: deptName }); setDeptName(""); load(); }
    catch (err) { setError(err.message); }
  };

  const addMember = async (teamId, userId) => {
    if (!userId) return;
    try { await api.addTeamMember(teamId, { user_id: Number(userId) }); load(); }
    catch (err) { setError(err.message); }
  };

  const userName = (id) => (users.find((u) => u.id === id) || {}).full_name || `#${id}`;
  const deptFor = (id) => (departments.find((d) => d.id === id) || {}).name || "—";
  const canCreate = can(me, "team.create");
  const canManage = can(me, "team.manage_members");

  return (
    <>
      {error && <div className="alert alert-danger py-2">{error}</div>}
      {canCreate && (
        <div className="row g-3" style={{ marginBottom: "1rem" }}>
          <div className="col-md-7">
            <form className="co-card row g-2 align-items-end m-0" onSubmit={createTeam}>
              <div className="col-6">
                <label className="form-label">New team</label>
                <input className="form-control" required value={teamForm.name}
                  onChange={(e) => setTeamForm({ ...teamForm, name: e.target.value })} />
              </div>
              <div className="col-4">
                <label className="form-label">Department</label>
                <select className="form-select" value={teamForm.department_id}
                  onChange={(e) => setTeamForm({ ...teamForm, department_id: e.target.value })}>
                  <option value="">—</option>
                  {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>
              <div className="col-2"><button className="btn btn-co w-100">Create</button></div>
            </form>
          </div>
          <div className="col-md-5">
            <form className="co-card row g-2 align-items-end m-0" onSubmit={createDept}>
              <div className="col-8">
                <label className="form-label">New department</label>
                <input className="form-control" required value={deptName}
                  onChange={(e) => setDeptName(e.target.value)} />
              </div>
              <div className="col-4"><button className="btn btn-co w-100">Create</button></div>
            </form>
          </div>
        </div>
      )}

      {teams.map((t) => (
        <div className="co-card" key={t.id}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <b>{t.name}</b>
              <span className="muted" style={{ fontSize: ".85rem" }}> · {deptFor(t.department_id)}</span>
              {t.manager_id && <span className="muted" style={{ fontSize: ".85rem" }}> · Manager: {userName(t.manager_id)}</span>}
            </div>
            {canManage && (
              <select className="form-select form-select-sm" style={{ width: 220 }}
                defaultValue="" onChange={(e) => { addMember(t.id, e.target.value); e.target.value = ""; }}>
                <option value="" disabled>+ Add member…</option>
                {users.filter((u) => !(t.members || []).includes(u.id))
                  .map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
              </select>
            )}
          </div>
          <div style={{ marginTop: ".5rem", display: "flex", flexWrap: "wrap", gap: ".4rem" }}>
            {(t.members || []).map((mid) => (
              <span key={mid} className="chip INFO">{userName(mid)}</span>
            ))}
            {(t.members || []).length === 0 && <span className="muted" style={{ fontSize: ".85rem" }}>No members.</span>}
          </div>
        </div>
      ))}
    </>
  );
};

// ---------------------------------------------------------------- Roles tab
const RolesTab = () => {
  const [roles, setRoles] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    api.roles().then((r) => { setRoles(r); if (r.length) setSelected(r[0].name); })
      .catch((e) => setError(e.message));
    api.permissionsCatalog().then(setCatalog).catch(() => {});
  }, []);

  const role = roles.find((r) => r.name === selected);
  // Group catalog codes by domain prefix for a readable matrix.
  const groups = {};
  catalog.forEach(({ code }) => {
    const domain = code.split(".")[0];
    (groups[domain] = groups[domain] || []).push(code);
  });

  return (
    <>
      {error && <div className="alert alert-danger py-2">{error}</div>}
      <div className="row g-3">
        <div className="col-md-4">
          <div className="co-card">
            <div className="section-title">Roles</div>
            {roles.map((r) => (
              <div key={r.id} className="work-row" style={{ cursor: "pointer" }}
                onClick={() => setSelected(r.name)}>
                <span className={`dotsev ${r.name === selected ? "HIGH" : "INFO"}`} />
                <div className="grow"><div className="title">{r.label || r.name}</div>
                  <div className="meta">{(r.permissions || []).length} permissions</div></div>
              </div>
            ))}
          </div>
        </div>
        <div className="col-md-8">
          <div className="co-card">
            <div className="section-title">Permissions — {selected || "…"}</div>
            {role && Object.entries(groups).map(([domain, codes]) => (
              <div key={domain} style={{ marginBottom: ".6rem" }}>
                <div className="muted" style={{ fontSize: ".75rem", textTransform: "uppercase", letterSpacing: ".06em", marginBottom: ".2rem" }}>{domain}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: ".35rem" }}>
                  {codes.map((code) => {
                    const has = (role.permissions || []).includes(code);
                    return (
                      <span key={code} className={`chip ${has ? "LOW" : "INFO"}`}
                        style={has ? {} : { opacity: 0.35 }}>
                        {has ? "✓" : "✗"} {code.split(".").slice(1).join(".")}
                      </span>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
};

// ----------------------------------------------------- Integrations tab
const HEALTH_SEV = { UP: "LOW", DEGRADED: "MEDIUM", DOWN: "CRITICAL", UNKNOWN: "INFO" };

const IntegrationsTab = ({ me }) => {
  const [providers, setProviders] = useState([]);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState(null);
  const [credForm, setCredForm] = useState({});

  const load = useCallback(() => {
    api.providers().then(setProviders).catch((e) => setError(e.message));
    api.webhookEvents().then(setEvents).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const canManage = can(me, "organization.update");

  const toggle = async (p) => {
    try { await api.updateProvider(p.id, { enabled: !p.enabled }); load(); }
    catch (e) { setError(e.message); }
  };
  const checkHealth = async (p) => {
    try { await api.providerHealth(p.id); load(); }
    catch (e) { setError(e.message); }
  };
  const saveCred = async (p) => {
    const f = credForm[p.id] || {};
    if (!f.key_name || !f.secret_value) return;
    try {
      await api.setProviderCredential(p.id, f);
      setCredForm({ ...credForm, [p.id]: {} });
      load();
    } catch (e) { setError(e.message); }
  };

  return (
    <>
      {error && <div className="alert alert-danger py-2">{error}</div>}
      <div className="co-card" style={{ marginBottom: "1rem" }}>
        <div className="section-title">Providers</div>
        {providers.map((p) => (
          <div key={p.id} style={{ padding: ".6rem 0", borderBottom: "1px solid var(--co-border)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: ".6rem" }}>
              <span className={`dotsev ${p.enabled ? "LOW" : "INFO"}`} />
              <div className="grow" style={{ flex: 1 }}>
                <b>{p.name}</b> <span className="muted" style={{ fontSize: ".82rem" }}>· {p.provider_type} · adapter: {p.adapter}</span>
                {p.health && <span className={`chip ${HEALTH_SEV[p.health.status] || "INFO"}`} style={{ marginLeft: ".4rem" }}>{p.health.status}</span>}
              </div>
              <span className="muted" style={{ fontSize: ".78rem" }}>
                creds: {p.credential_keys.length ? p.credential_keys.join(", ") : "none"}
              </span>
              {canManage && <button className="btn btn-sm btn-outline-secondary" onClick={() => checkHealth(p)}>Health</button>}
              {canManage && (
                <button className={`btn btn-sm ${p.enabled ? "btn-outline-danger" : "btn-outline-success"}`} onClick={() => toggle(p)}>
                  {p.enabled ? "Disable" : "Enable"}
                </button>
              )}
            </div>
            {canManage && (
              <div className="row g-1 align-items-end" style={{ marginTop: ".4rem", paddingLeft: "1.4rem" }}>
                <div className="col-3">
                  <input className="form-control form-control-sm" placeholder="key (e.g. api_key)"
                    value={(credForm[p.id] || {}).key_name || ""}
                    onChange={(e) => setCredForm({ ...credForm, [p.id]: { ...(credForm[p.id] || {}), key_name: e.target.value } })} />
                </div>
                <div className="col-4">
                  <input type="password" className="form-control form-control-sm" placeholder="secret (never displayed)"
                    value={(credForm[p.id] || {}).secret_value || ""}
                    onChange={(e) => setCredForm({ ...credForm, [p.id]: { ...(credForm[p.id] || {}), secret_value: e.target.value } })} />
                </div>
                <div className="col-2">
                  <button className="btn btn-sm btn-co w-100" onClick={() => saveCred(p)}>Save credential</button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="co-card">
        <div className="section-title">Recent webhook events</div>
        {events.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No webhook events yet.</div>}
        {events.map((e) => (
          <div className="work-row" key={e.id}>
            <span className={`dotsev ${e.status === "PROCESSED" ? "LOW" : e.status === "ERROR" ? "CRITICAL" : "INFO"}`} />
            <div className="grow">
              <div className="title">{e.provider} · {e.external_event_id}</div>
              <div className="meta">signature {e.signature_valid ? "valid" : "invalid"}</div>
            </div>
            <span className={`chip ${e.status === "PROCESSED" ? "LOW" : e.status === "ERROR" ? "CRITICAL" : "INFO"}`}>{e.status}</span>
          </div>
        ))}
      </div>
    </>
  );
};

// ------------------------------------------------------- Risk Model tab
const RiskModelTab = () => {
  const [meths, setMeths] = useState([]);
  const [error, setError] = useState(null);
  useEffect(() => {
    api.riskMethodologies().then(setMeths).catch((e) => setError(e.message));
  }, []);
  const CT = { FLAG: "flag", COUNTRY_IN: "country in list", ACTIVITY_IN: "activity in list" };
  return (
    <>
      {error && <div className="alert alert-danger py-2">{error}</div>}
      {meths.length === 0 && <div className="empty">No risk methodology configured.</div>}
      {meths.map((m) => (
        <div className="co-card" key={m.id}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div className="section-title" style={{ marginBottom: 0 }}>
              {m.name} <span className="muted" style={{ fontWeight: 400 }}>· version {m.version}</span>
            </div>
            <span className={`chip ${m.active ? "LOW" : "INFO"}`}>
              {m.active ? "ACTIVE" : "inactive"}{m.organization_id ? " · org" : " · system"}
            </span>
          </div>
          <div style={{ overflowX: "auto", marginTop: ".5rem" }}>
            <table className="table table-sm align-middle" style={{ fontSize: ".9rem" }}>
              <thead><tr className="muted"><th>Factor</th><th>Condition</th><th style={{ textAlign: "right" }}>Impact</th></tr></thead>
              <tbody>
                {m.factors.map((f) => (
                  <tr key={f.id}>
                    <td><b>{f.code}</b> <span className="muted">· {f.label}</span></td>
                    <td className="muted">{CT[f.condition_type] || f.condition_type}
                      {f.condition_value.field ? `: ${f.condition_value.field}` : ""}
                      {f.condition_value.values ? `: ${f.condition_value.values.join(", ")}` : ""}</td>
                    <td style={{ textAlign: "right", color: "var(--sev-high)", fontWeight: 700 }}>+{f.impact}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: ".35rem" }}>
            {m.thresholds.map((t) => (
              <span key={t.id} className={`chip ${t.level}`}>
                {t.level}: {t.min_score}{t.max_score != null ? `–${t.max_score}` : "+"}
              </span>
            ))}
          </div>
        </div>
      ))}
    </>
  );
};

// ------------------------------------------------------- Organization tab
const OrganizationTab = ({ me }) => {
  const [data, setData] = useState(null);
  const [name, setName] = useState("");
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.organization().then((d) => { setData(d); setName(d.organization.name); })
      .catch((e) => setError(e.message));
  }, []);

  const save = async (e) => {
    e.preventDefault();
    setError(null); setSaved(false);
    try { await api.updateOrganization({ name }); setSaved(true); }
    catch (err) { setError(err.message); }
  };

  if (!data) return <div className="empty">Loading…</div>;
  return (
    <div className="co-card" style={{ maxWidth: 560 }}>
      <div className="section-title">Organization settings</div>
      {error && <div className="alert alert-danger py-2">{error}</div>}
      {saved && <div className="alert alert-success py-2">Saved.</div>}
      <form onSubmit={save}>
        <label className="form-label">Organization name</label>
        <input className="form-control" value={name} onChange={(e) => setName(e.target.value)} />
        <div className="muted mt-2" style={{ fontSize: ".85rem" }}>
          {data.member_count} member(s) · {data.departments.length} department(s) · {data.teams.length} team(s)
        </div>
        {can(me, "organization.update") && <button className="btn btn-co mt-3">Save</button>}
      </form>
    </div>
  );
};

// ---------------------------------------------------------------- Page
export const Administration = () => {
  const { store } = useGlobalReducer();
  const me = store.user;
  const tabs = [
    { key: "users", label: "Users", icon: "fa-user-group", permission: "user.view" },
    { key: "teams", label: "Teams & Departments", icon: "fa-sitemap", permission: "team.view" },
    { key: "roles", label: "Roles & Permissions", icon: "fa-shield-halved", permission: "role.view" },
    { key: "risk", label: "Risk Model", icon: "fa-gauge-high", permission: "risk.view" },
    { key: "integrations", label: "Integrations", icon: "fa-plug", permission: "organization.view" },
    { key: "organization", label: "Organization", icon: "fa-building", permission: "organization.view" },
  ].filter((t) => can(me, t.permission));
  const [tab, setTab] = useState(tabs.length ? tabs[0].key : null);

  if (!tabs.length) return <div className="empty">You do not have administration access.</div>;

  return (
    <>
      <h3 style={{ marginBottom: "1rem" }}>Administration</h3>
      <ul className="nav nav-pills" style={{ marginBottom: "1.25rem", gap: ".25rem" }}>
        {tabs.map((t) => (
          <li className="nav-item" key={t.key}>
            <button className={`nav-link ${tab === t.key ? "active" : ""}`}
              style={tab === t.key ? { background: "var(--co-primary)" } : {}}
              onClick={() => setTab(t.key)}>
              <i className={`fa-solid ${t.icon}`} /> {t.label}
            </button>
          </li>
        ))}
      </ul>
      {tab === "users" && <UsersTab me={me} />}
      {tab === "teams" && <TeamsTab me={me} />}
      {tab === "roles" && <RolesTab />}
      {tab === "risk" && <RiskModelTab />}
      {tab === "integrations" && <IntegrationsTab me={me} />}
      {tab === "organization" && <OrganizationTab me={me} />}
    </>
  );
};
