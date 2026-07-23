import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { can } from "../permissions/can";

const fmtDue = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

export const Workspace = () => {
  const { store } = useGlobalReducer();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);   // task id being completed

  const load = () => api.workspace().then(setData).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  // A task is closed from the list where it lives — cases keep their own
  // decision flow on the case page.
  const completeTask = async (id) => {
    setBusy(id); setError(null);
    try { await api.completeTask(id); await load(); }
    catch (e) { setError(e.message); }
    finally { setBusy(null); }
  };

  if (error && !data) return <div className="alert alert-danger">{error}</div>;
  if (!data) return <div className="empty">Loading your workspace…</div>;

  const c = data.counters;

  return (
    <>
      <h3 style={{ marginBottom: ".25rem" }}>Good day, {data.greeting_name} 👋</h3>
      <p className="muted">Here is what needs your attention. Role: <b>{data.role}</b></p>

      {error && <div className="alert alert-danger py-2">{error}</div>}

      <div className="co-counters" style={{ marginBottom: "1.25rem" }}>
        <div className="co-counter urgent"><div className="n">{c.urgent}</div><div className="l">Urgent cases</div></div>
        <div className="co-counter due"><div className="n">{c.due_today}</div><div className="l">Due today</div></div>
        <div className="co-counter cases"><div className="n">{c.open_cases}</div><div className="l">Open cases</div></div>
        <div className="co-counter tasks"><div className="n">{c.open_tasks}</div><div className="l">Open tasks</div></div>
      </div>

      <div className="co-card">
        <div className="section-title">My Work — prioritized</div>
        {data.my_work.length === 0 && (
          <div className="empty">
            Nothing pending. Open a <Link to="/customers">customer</Link> and run screening to generate work.
          </div>
        )}
        {data.my_work.map((item) => {
          const to = item.kind === "case" ? `/cases/${item.id}` : `/customers/${item.customer_id}`;
          return (
            <div className="work-row" key={`${item.kind}-${item.id}`}>
              <span className={`dotsev ${item.priority}`} />
              <div className="grow">
                <div className="title">
                  <Link to={to}>{item.title}</Link>
                </div>
                <div className="meta">
                  {item.kind === "case" ? "Case" : "Task"} · {item.customer || "—"} · due {fmtDue(item.due_at)}
                </div>
              </div>
              <span className={`chip ${item.priority}`}>{item.priority}</span>
              <Link to={to} className="btn btn-sm btn-outline-secondary">
                {item.kind === "case" ? "Investigate" : "Open"}
              </Link>
              {item.kind !== "case" && can(store.user, "task.complete") && (
                <button className="btn btn-sm btn-outline-success"
                  disabled={busy === item.id}
                  title="Mark this task as done (audited)"
                  onClick={() => completeTask(item.id)}>
                  <i className="fa-solid fa-check" /> Done
                </button>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
};
