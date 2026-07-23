import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";
import { can } from "../permissions/can";
import { AlertDetails } from "../components/AlertDetails";

const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : "—");

// The Alert Center is view-per-question, not one long list: landing on it
// answers "what has no owner yet?"; one click answers "what is on MY plate?".
// History (resolved / dismissed) stays reachable but out of the way.
const VIEWS = [
  { key: "UNASSIGNED", label: "Unassigned", icon: "fa-inbox" },
  { key: "MINE", label: "My alerts", icon: "fa-user-check" },
  { key: "RESOLVED", label: "Resolved", icon: "fa-check" },
  { key: "DISMISSED", label: "Dismissed", icon: "fa-ban" },
];

export const Alerts = () => {
  const { store } = useGlobalReducer();
  const me = store.user;
  const [active, setActive] = useState([]);      // OPEN/ASSIGNED/IN_REVIEW
  const [history, setHistory] = useState([]);    // when a history view is on
  const [view, setView] = useState("UNASSIGNED");
  const [candidates, setCandidates] = useState([]);
  const [error, setError] = useState(null);
  const [resolving, setResolving] = useState(null);
  const [resolution, setResolution] = useState("");
  const [expanded, setExpanded] = useState(null);

  const isHistory = ["RESOLVED", "DISMISSED"].includes(view);
  const canAssign = can(me, "case.assign");
  const canResolve = can(me, "case.update");

  const load = useCallback(() => {
    api.alerts(null).then(setActive).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (isHistory) api.alerts(view).then(setHistory).catch((e) => setError(e.message));
  }, [view, isHistory]);
  // Who alerts can be handed to (team-mates, or all staff when teamless).
  useEffect(() => {
    if (canAssign) api.assignableUsers().then(setCandidates).catch(() => {});
  }, [canAssign]);

  const assign = async (a, userId) => {
    try {
      await api.assignAlert(a.id, userId ? { user_id: userId } : {});
      load();
    } catch (e) { setError(e.message); }
  };
  const doResolve = async (a, dismiss) => {
    if (!resolution.trim()) { setError("A resolution note is required."); return; }
    try {
      await api.resolveAlert(a.id, { resolution, dismiss });
      setResolving(null); setResolution(""); load();
      if (isHistory) api.alerts(view).then(setHistory).catch(() => {});
    } catch (e) { setError(e.message); }
  };

  const unassigned = active.filter((a) => !a.assigned_to);
  const mine = active.filter((a) => a.assigned_to === me?.id);
  const shown = view === "UNASSIGNED" ? unassigned
    : view === "MINE" ? mine : history;
  const counts = shown.reduce((acc, a) => { acc[a.severity] = (acc[a.severity] || 0) + 1; return acc; }, {});

  const viewCount = (key) =>
    key === "UNASSIGNED" ? ` (${unassigned.length})`
      : key === "MINE" ? ` (${mine.length})` : "";

  // Plain render function, not a nested component — a component type recreated
  // each render would remount and drop input focus on every keystroke.
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
            {a.assigned_to_name && view !== "MINE" ? <> · <b>{a.assigned_to_name}</b></> : ""}
          </div>
        </div>
        <span className={`chip ${a.severity}`}>{a.severity}</span>
        {isHistory && (
          <span className={`chip ${a.status === "RESOLVED" ? "LOW" : "INFO"}`}>{a.status}</span>
        )}
        <button className="btn btn-sm btn-outline-secondary"
          onClick={() => setExpanded(expanded === a.id ? null : a.id)}>
          {expanded === a.id ? "Hide" : "Details"}
        </button>
        {!isHistory && canAssign && !a.assigned_to && (
          <button className="btn btn-sm btn-outline-secondary" onClick={() => assign(a)}>Assign to me</button>
        )}
        {!isHistory && canAssign && candidates.length > 0 && (
          <select className="form-select form-select-sm" style={{ width: 150 }}
            value="" title="Hand this alert to a team member"
            onChange={(e) => e.target.value && assign(a, Number(e.target.value))}>
            <option value="">{a.assigned_to ? "Reassign to…" : "Assign to…"}</option>
            {candidates.map((c) => (
              <option key={c.id} value={c.id}>{c.full_name || c.email}</option>
            ))}
          </select>
        )}
        {!isHistory && canResolve && (
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

  const EMPTY = {
    UNASSIGNED: "No unassigned alerts — everything has an owner. 🎉",
    MINE: "Nothing assigned to you.",
    RESOLVED: "No resolved alerts yet.",
    DISMISSED: "No dismissed alerts.",
  };

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: ".6rem", marginBottom: "1rem" }}>
        <h3 style={{ margin: 0 }}>Alert Center</h3>
        <div className="pt-tabs" style={{ margin: 0 }}>
          {VIEWS.map((v) => (
            <button key={v.key} className={"pt-tab" + (view === v.key ? " active" : "")}
              onClick={() => setView(v.key)}>
              <i className={`fa-solid ${v.icon}`} /> {v.label}{viewCount(v.key)}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="alert alert-danger py-2">{error}</div>}

      <div className="co-counters" style={{ marginBottom: "1.25rem" }}>
        <div className="co-counter urgent"><div className="n">{counts.CRITICAL || 0}</div><div className="l">Critical</div></div>
        <div className="co-counter due"><div className="n">{counts.HIGH || 0}</div><div className="l">High</div></div>
        <div className="co-counter cases"><div className="n">{counts.MEDIUM || 0}</div><div className="l">Medium</div></div>
        <div className="co-counter tasks"><div className="n">{shown.length}</div><div className="l">In this view</div></div>
      </div>

      <div className="co-card">
        <div className="section-title">
          {VIEWS.find((v) => v.key === view).label}{viewCount(view) || ` (${shown.length})`}
        </div>
        {shown.length === 0 && <div className="empty">{EMPTY[view]}</div>}
        <div className="co-rows">{shown.map(renderRow)}</div>
      </div>
    </>
  );
};
