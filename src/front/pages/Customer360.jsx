import { useEffect, useState, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../services/api";

const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : "—");

export const Customer360 = () => {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [screening, setScreening] = useState(false);

  const load = useCallback(() => api.customer(id).then(setData).catch((e) => setError(e.message)), [id]);
  useEffect(() => { load(); }, [load]);

  const runScreening = async () => {
    setScreening(true);
    try {
      await api.screen(id);
      // The chain may run asynchronously via Celery — poll a few times.
      let tries = 0;
      const poll = setInterval(async () => {
        tries += 1;
        await load();
        if (tries >= 4) { clearInterval(poll); setScreening(false); }
      }, 1200);
    } catch (e) { setError(e.message); setScreening(false); }
  };

  if (error) return <div className="alert alert-danger">{error}</div>;
  if (!data) return <div className="empty">Loading customer…</div>;

  const { customer, risk, open_cases, tasks, documents, recent_events, changes_since_review } = data;

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1rem" }}>
        <div>
          <div className="muted" style={{ fontSize: ".8rem" }}><Link to="/customers">← Customers</Link></div>
          <h3 style={{ margin: ".2rem 0" }}>{customer.name}</h3>
          <div className="muted">
            {customer.customer_type} · {customer.country || "—"}
            {customer.business_activity ? ` · ${customer.business_activity}` : ""}
          </div>
        </div>
        <button className="btn btn-co" onClick={runScreening} disabled={screening}>
          <i className="fa-solid fa-magnifying-glass" /> {screening ? "Screening…" : "Run screening"}
        </button>
      </div>

      <div className="row g-3">
        {/* Risk — explainable */}
        <div className="col-md-5">
          <div className="co-card">
            <div className="section-title">Risk assessment</div>
            <div style={{ display: "flex", alignItems: "baseline", gap: ".6rem" }}>
              <span className="risk-badge">{customer.risk_score}<small> / 100</small></span>
              <span className={`chip ${customer.risk_level}`}>{customer.risk_level}</span>
            </div>

            {risk && risk.factors && risk.factors.length > 0 ? (
              <div style={{ marginTop: ".75rem" }}>
                <div className="muted" style={{ fontSize: ".8rem", marginBottom: ".25rem" }}>Why?</div>
                {risk.factors.map((f) => (
                  <div className="factor" key={f.code}>
                    <span>{f.label}</span>
                    <span className="impact">+{f.impact}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted mt-2" style={{ fontSize: ".85rem" }}>No risk drivers yet. Run screening.</p>
            )}

            {risk && risk.required_actions && risk.required_actions.length > 0 && (
              <div style={{ marginTop: ".75rem" }}>
                <div className="muted" style={{ fontSize: ".8rem", marginBottom: ".25rem" }}>Required actions</div>
                <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                  {risk.required_actions.map((a, i) => <li key={i} style={{ fontSize: ".88rem" }}>{a}</li>)}
                </ul>
              </div>
            )}
          </div>
        </div>

        <div className="col-md-7">
          {/* Changes since review */}
          <div className="co-card">
            <div className="section-title">Changes since last review</div>
            {changes_since_review.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No changes detected.</div>}
            {changes_since_review.map((e) => (
              <div className="work-row" key={e.id}>
                <span className={`dotsev ${e.severity}`} />
                <div className="grow">
                  <div className="title">{e.event_type.replace(/_/g, " ")}</div>
                  <div className="meta">{e.source} · {fmt(e.detected_at)}</div>
                </div>
                <span className={`chip ${e.severity}`}>{e.severity}</span>
              </div>
            ))}
          </div>

          {/* Open cases */}
          <div className="co-card">
            <div className="section-title">Open cases</div>
            {open_cases.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No open cases.</div>}
            {open_cases.map((c) => (
              <div className="work-row" key={c.id}>
                <span className={`dotsev ${c.priority}`} />
                <div className="grow">
                  <div className="title"><Link to={`/cases/${c.id}`}>{c.title}</Link></div>
                  <div className="meta">{c.case_type} · {c.status}</div>
                </div>
                <Link to={`/cases/${c.id}`} className="btn btn-sm btn-outline-secondary">Investigate</Link>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="row g-3 mt-0">
        <div className="col-md-6">
          <div className="co-card">
            <div className="section-title">Open tasks</div>
            {tasks.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>None.</div>}
            {tasks.map((t) => (
              <div className="work-row" key={t.id}>
                <span className={`dotsev ${t.priority}`} />
                <div className="grow"><div className="title">{t.title}</div><div className="meta">{t.task_type}</div></div>
                <span className={`chip ${t.priority}`}>{t.priority}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="col-md-6">
          <div className="co-card">
            <div className="section-title">Recent events</div>
            {recent_events.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No events yet.</div>}
            {recent_events.map((e) => (
              <div className="tl-item" key={e.id}>
                <span className="when">{fmt(e.detected_at)}</span>
                <span className={`dotsev ${e.severity}`} style={{ marginTop: 4 }} />
                <span style={{ fontSize: ".88rem" }}>{e.event_type.replace(/_/g, " ")}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
};
