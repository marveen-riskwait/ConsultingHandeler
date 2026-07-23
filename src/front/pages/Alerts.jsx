import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";
import { can } from "../permissions/can";
import { AlertDetails } from "../components/AlertDetails";

const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : "—");

export const Alerts = () => {
  const { store } = useGlobalReducer();
  const me = store.user;
  const [alerts, setAlerts] = useState([]);
  const [filter, setFilter] = useState("OPEN_ALL");
  const [error, setError] = useState(null);
  const [resolving, setResolving] = useState(null);
  const [resolution, setResolution] = useState("");
  const [expanded, setExpanded] = useState(null);

  const load = useCallback(() => {
    const status = ["OPEN", "ASSIGNED", "IN_REVIEW", "RESOLVED", "DISMISSED"].includes(filter) ? filter : null;
    api.alerts(status).then(setAlerts).catch((e) => setError(e.message));
  }, [filter]);
  useEffect(() => { load(); }, [load]);

  const assignToMe = async (a) => {
    try { await api.assignAlert(a.id, {}); load(); } catch (e) { setError(e.message); }
  };
  const doResolve = async (a, dismiss) => {
    if (!resolution.trim()) { setError("A resolution note is required."); return; }
    try {
      await api.resolveAlert(a.id, { resolution, dismiss });
      setResolving(null); setResolution(""); load();
    } catch (e) { setError(e.message); }
  };

  const counts = alerts.reduce((acc, a) => { acc[a.severity] = (acc[a.severity] || 0) + 1; return acc; }, {});
  const canAssign = can(me, "case.assign");
  const canResolve = can(me, "case.update");

  // A plain render function, NOT a nested component: a component type defined
  // inside render would remount on every keystroke and the resolution input
  // would lose focus after each character.
  const renderRow = (a) => (
    <div key={a.id} style={{ borderBottom: "1px solid var(--co-border)", padding: ".7rem 0" }}>
      <div className="work-row" style={{ borderBottom: "none", padding: 0 }}>
        <span className={`dotsev ${a.severity}`} />
        <div className="grow">
          <div className="title">
            {a.title}
            {a.customer_id && <Link to={`/customers/${a.customer_id}`} style={{ fontWeight: 400 }}> · {a.customer_name}</Link>}
          </div>
          <div className="meta">
            {a.source} · {fmt(a.created_at)}
            {a.assigned_to_name ? <> · <b>{a.assigned_to_name}</b></> : ""}
          </div>
        </div>
        <span className={`chip ${a.severity}`}>{a.severity}</span>
        <span className={`chip ${a.status === "RESOLVED" ? "LOW" : a.status === "DISMISSED" ? "INFO" : "MEDIUM"}`}>{a.status}</span>
        <button className="btn btn-sm btn-outline-secondary"
          onClick={() => setExpanded(expanded === a.id ? null : a.id)}>
          {expanded === a.id ? "Hide" : "Details"}
        </button>
        {!["RESOLVED", "DISMISSED"].includes(a.status) && canAssign && !a.assigned_to && (
          <button className="btn btn-sm btn-outline-secondary" onClick={() => assignToMe(a)}>Assign to me</button>
        )}
        {!["RESOLVED", "DISMISSED"].includes(a.status) && canResolve && (
          <button className="btn btn-sm btn-outline-success" onClick={() => { setResolving(resolving === a.id ? null : a.id); setResolution(""); }}>Resolve</button>
        )}
      </div>
      {expanded === a.id && <AlertDetails details={a.details} />}
      {resolving === a.id && (
        <div className="row g-1 align-items-end" style={{ marginTop: ".4rem", paddingLeft: "1.4rem" }}>
          <div className="col-12 col-md-7">
            <input className="form-control form-control-sm" placeholder="Resolution note (audited)"
              value={resolution} autoFocus onChange={(e) => setResolution(e.target.value)} />
          </div>
          <div className="col-6 col-md-2"><button className="btn btn-sm btn-outline-success w-100" onClick={() => doResolve(a, false)}>Resolve</button></div>
          <div className="col-6 col-md-2"><button className="btn btn-sm btn-outline-secondary w-100" onClick={() => doResolve(a, true)}>Dismiss</button></div>
        </div>
      )}
      {a.resolution && <div className="muted" style={{ fontSize: ".82rem", paddingLeft: "1.4rem" }}>Resolution: {a.resolution}</div>}
    </div>
  );

  // Two piles for active work: what still needs an owner, then what is being
  // handled (and by whom). History views keep a single flat list.
  const history = ["RESOLVED", "DISMISSED"].includes(filter);
  const unassigned = alerts.filter((a) => !a.assigned_to);
  const assigned = alerts.filter((a) => a.assigned_to);

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: ".6rem", marginBottom: "1rem" }}>
        <h3 style={{ margin: 0 }}>Alert Center</h3>
        <select className="form-select" style={{ width: 200 }} value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="OPEN_ALL">Open (all active)</option>
          <option value="OPEN">Open</option>
          <option value="ASSIGNED">Assigned</option>
          <option value="RESOLVED">Resolved</option>
          <option value="DISMISSED">Dismissed</option>
        </select>
      </div>

      {error && <div className="alert alert-danger py-2">{error}</div>}

      <div className="co-counters" style={{ marginBottom: "1.25rem" }}>
        <div className="co-counter urgent"><div className="n">{counts.CRITICAL || 0}</div><div className="l">Critical</div></div>
        <div className="co-counter due"><div className="n">{counts.HIGH || 0}</div><div className="l">High</div></div>
        <div className="co-counter cases"><div className="n">{counts.MEDIUM || 0}</div><div className="l">Medium</div></div>
        <div className="co-counter tasks"><div className="n">{alerts.length}</div><div className="l">Total shown</div></div>
      </div>

      {history ? (
        <div className="co-card">
          <div className="section-title">{filter === "RESOLVED" ? "Resolved" : "Dismissed"} ({alerts.length})</div>
          {alerts.length === 0 && <div className="empty">Nothing here.</div>}
          <div className="co-rows">{alerts.map(renderRow)}</div>
        </div>
      ) : (
        <>
          <div className="co-card">
            <div className="section-title">
              <i className="fa-solid fa-inbox" /> Needs an owner ({unassigned.length})
            </div>
            {unassigned.length === 0 && <div className="empty">Every alert has an owner. 🎉</div>}
            <div className="co-rows">{unassigned.map(renderRow)}</div>
          </div>

          <div className="co-card" style={{ marginTop: "1rem" }}>
            <div className="section-title">
              <i className="fa-solid fa-user-check" /> Being handled ({assigned.length})
            </div>
            {assigned.length === 0 && <div className="empty">None in progress.</div>}
            <div className="co-rows">{assigned.map(renderRow)}</div>
          </div>
        </>
      )}
    </>
  );
};
