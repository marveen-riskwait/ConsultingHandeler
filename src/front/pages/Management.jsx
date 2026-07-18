import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";
import { can } from "../permissions/can";

const loadColor = (score) =>
  score >= 80 ? "var(--sev-critical)" : score >= 55 ? "var(--sev-high)"
    : score >= 30 ? "var(--sev-medium)" : "var(--sev-low)";

// ------------------------------------------------------------- Dashboard
const DashboardTab = () => {
  const [d, setD] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.managementDashboard().then(setD).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="alert alert-danger py-2">{error}</div>;
  if (!d) return <div className="empty">Loading operations…</div>;

  return (
    <>
      <div className="co-counters" style={{ marginBottom: "1.25rem" }}>
        <div className="co-counter cases"><div className="n">{d.open_cases}</div><div className="l">Open cases</div></div>
        <div className="co-counter urgent"><div className="n">{d.unassigned_cases}</div><div className="l">Unassigned</div></div>
        <div className="co-counter due"><div className="n">{d.overdue_tasks}</div><div className="l">Overdue tasks</div></div>
        <div className="co-counter urgent"><div className="n">{d.high_risk_cases}</div><div className="l">High-risk cases</div></div>
        <div className="co-counter tasks"><div className="n">{d.high_risk_customers}</div><div className="l">High-risk customers</div></div>
      </div>

      <div className="row g-3">
        <div className="col-md-7">
          <div className="co-card">
            <div className="section-title">Team workload</div>
            {d.team_workload.map((w) => (
              <div key={w.user_id} style={{ padding: ".45rem 0" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: ".9rem" }}>
                  <span><b>{w.name}</b> <span className="muted">· {w.role}</span></span>
                  <span className="muted">
                    {w.active_cases} cases · {w.overdue_tasks} overdue
                    {w.overdue_tasks > 0 && " 🔴"}
                  </span>
                </div>
                <div style={{ background: "var(--co-border)", borderRadius: 6, height: 8, marginTop: 4 }}>
                  <div style={{ width: `${w.workload_score}%`, height: 8, borderRadius: 6,
                    background: loadColor(w.workload_score) }} />
                </div>
                <div className="muted" style={{ fontSize: ".75rem", marginTop: 2 }}>
                  Workload {w.workload_score}%
                  {w.average_resolution_hours != null && ` · avg resolution ${w.average_resolution_hours}h`}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="col-md-5">
          <div className="co-card">
            <div className="section-title">SLA performance</div>
            <div style={{ display: "flex", alignItems: "baseline", gap: ".5rem" }}>
              <span className="risk-badge">{d.sla.on_time_pct}%</span>
              <span className="muted">on time</span>
            </div>
            <div className="factor"><span>🟢 On time</span><b>{d.sla.on_time}</b></div>
            <div className="factor"><span>🟠 At risk</span><b>{d.sla.at_risk}</b></div>
            <div className="factor"><span>🔴 Breached</span><b>{d.sla.breached}</b></div>
          </div>
          <div className="co-card">
            <div className="section-title">Performance (30 days)</div>
            <div className="factor"><span>Cases closed</span><b>{d.cases_closed_30d}</b></div>
            <div className="factor"><span>Avg resolution</span>
              <b>{d.average_resolution_hours != null ? `${d.average_resolution_hours}h` : "—"}</b></div>
            <div className="factor"><span>Escalation rate</span><b>{d.escalation_rate_pct}%</b></div>
          </div>
          <div className="co-card">
            <div className="section-title">Cases by status</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: ".35rem" }}>
              {Object.entries(d.cases_by_status).length === 0 &&
                <span className="muted" style={{ fontSize: ".88rem" }}>No open cases.</span>}
              {Object.entries(d.cases_by_status).map(([s, n]) => (
                <span key={s} className="chip INFO">{s} · {n}</span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

// ---------------------------------------------------------------- Queues
const QueuesTab = ({ me }) => {
  const [queue, setQueue] = useState([]);
  const [users, setUsers] = useState([]);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  const load = useCallback(() => {
    api.managementQueues().then(setQueue).catch((e) => setError(e.message));
    api.users().then(setUsers).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const assign = async (caseId, userId) => {
    setError(null);
    try {
      await api.assignCase(caseId, userId ? { user_id: Number(userId) } : { strategy: "LEAST_LOADED" });
      load();
    } catch (err) { setError(err.message); }
  };

  const bulk = async () => {
    setError(null);
    try {
      const r = await api.bulkAssign("LEAST_LOADED");
      setMessage(`Assigned ${r.assigned} case(s), ${r.remaining} remaining.`);
      load();
    } catch (err) { setError(err.message); }
  };

  const canAssign = can(me, "management.assign_work");

  return (
    <>
      {error && <div className="alert alert-danger py-2">{error}</div>}
      {message && <div className="alert alert-success py-2">{message}</div>}
      <div className="co-card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: ".5rem" }}>
          <div className="section-title" style={{ marginBottom: 0 }}>Unassigned work ({queue.length})</div>
          {canAssign && queue.length > 0 && (
            <button className="btn btn-sm btn-co" onClick={bulk}>
              <i className="fa-solid fa-wand-magic-sparkles" /> Bulk assign (least loaded)
            </button>
          )}
        </div>
        {queue.length === 0 && <div className="empty">Queue is empty — everything is assigned. 🎉</div>}
        {queue.map((c) => (
          <div className="work-row" key={c.id}>
            <span className={`dotsev ${c.priority}`} />
            <div className="grow">
              <div className="title"><Link to={`/cases/${c.id}`}>{c.title}</Link></div>
              <div className="meta">
                {c.customer_name} · {c.case_type}
                {c.age_hours != null && ` · ${c.age_hours}h old`}
              </div>
            </div>
            <span className={`chip ${c.priority}`}>{c.priority}</span>
            {canAssign && (
              <select className="form-select form-select-sm" style={{ width: 180 }}
                defaultValue="" onChange={(e) => { assign(c.id, e.target.value); e.target.value = ""; }}>
                <option value="" disabled>Assign to…</option>
                <option value="">Auto (least loaded)</option>
                {users.filter((u) => !["CUSTOMER_USER", "AUDITOR"].includes(u.role))
                  .map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
              </select>
            )}
          </div>
        ))}
      </div>
    </>
  );
};

// -------------------------------------------------------------- Workload
const WorkloadTab = () => {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.managementWorkload().then(setRows).catch((e) => setError(e.message));
  }, []);

  return (
    <>
      {error && <div className="alert alert-danger py-2">{error}</div>}
      <div className="co-card">
        <div className="section-title">Workload by analyst</div>
        <div style={{ overflowX: "auto" }}>
          <table className="table table-sm align-middle" style={{ fontSize: ".9rem" }}>
            <thead>
              <tr className="muted">
                <th>Member</th><th>Role</th><th>Active cases</th><th>Active tasks</th>
                <th>Overdue</th><th>High risk</th><th>Avg resolution</th><th style={{ minWidth: 140 }}>Load</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((w) => (
                <tr key={w.user_id}>
                  <td><b>{w.name}</b></td>
                  <td className="muted">{w.role}</td>
                  <td>{w.active_cases}</td>
                  <td>{w.active_tasks}</td>
                  <td>{w.overdue_tasks > 0 ? <span style={{ color: "var(--sev-critical)" }}>{w.overdue_tasks} 🔴</span> : 0}</td>
                  <td>{w.high_risk_cases}</td>
                  <td>{w.average_resolution_hours != null ? `${w.average_resolution_hours}h` : "—"}</td>
                  <td>
                    <div style={{ background: "var(--co-border)", borderRadius: 6, height: 8 }}>
                      <div style={{ width: `${w.workload_score}%`, height: 8, borderRadius: 6,
                        background: loadColor(w.workload_score) }} />
                    </div>
                    <span className="muted" style={{ fontSize: ".75rem" }}>{w.workload_score}%</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
};

// ------------------------------------------------------------------ Page
export const Management = () => {
  const { store } = useGlobalReducer();
  const me = store.user;
  const tabs = [
    { key: "dashboard", label: "Operations", icon: "fa-gauge-high", permission: "management.view" },
    { key: "queues", label: "Queues", icon: "fa-list-check", permission: "management.view" },
    { key: "workload", label: "Workload", icon: "fa-scale-balanced", permission: "management.team_view" },
  ].filter((t) => can(me, t.permission));
  const [tab, setTab] = useState(tabs.length ? tabs[0].key : null);

  if (!tabs.length) return <div className="empty">You do not have management access.</div>;

  return (
    <>
      <h3 style={{ marginBottom: "1rem" }}>Management</h3>
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
      {tab === "dashboard" && <DashboardTab />}
      {tab === "queues" && <QueuesTab me={me} />}
      {tab === "workload" && <WorkloadTab />}
    </>
  );
};
