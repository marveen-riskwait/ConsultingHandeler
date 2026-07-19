import { useEffect, useState, useCallback } from "react";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";
import { can } from "../permissions/can";

const fmt = (iso) => (iso ? new Date(iso).toLocaleDateString() : "—");
const CTRL_SEV = { IMPLEMENTED: "LOW", PARTIAL: "MEDIUM", NEEDS_REVIEW: "MEDIUM", MISSING: "CRITICAL" };

export const Regulatory = () => {
  const { store } = useGlobalReducer();
  const me = store.user;
  const [data, setData] = useState(null);
  const [sources, setSources] = useState([]);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    api.regulatory().then(setData).catch((e) => setError(e.message));
    api.regulatorySources().then(setSources).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const assess = async (id) => {
    const notes = window.prompt("Assessment notes (optional):") || "";
    try { await api.assessRegulatoryChange(id, notes); load(); }
    catch (e) { setError(e.message); }
  };

  if (error) return <div className="alert alert-danger">{error}</div>;
  if (!data) return <div className="empty">Loading regulatory intelligence…</div>;

  const canManage = can(me, "regulatory.manage");
  const controlsByReq = {};
  data.controls.forEach((c) => { (controlsByReq[c.requirement_id] = controlsByReq[c.requirement_id] || []).push(c); });

  return (
    <>
      <h3 style={{ marginBottom: "1rem" }}>Regulatory Intelligence</h3>

      <div className="co-counters" style={{ marginBottom: "1.25rem" }}>
        <div className="co-counter urgent"><div className="n">{data.impact_counts.HIGH}</div><div className="l">High-impact changes</div></div>
        <div className="co-counter due"><div className="n">{data.impact_counts.MEDIUM}</div><div className="l">Medium impact</div></div>
        <div className="co-counter cases"><div className="n">{data.requirement_count}</div><div className="l">Requirements</div></div>
        <div className="co-counter tasks"><div className="n">{data.controls.length}</div><div className="l">Controls</div></div>
      </div>

      <div className="row g-3">
        <div className="col-md-7">
          <div className="co-card">
            <div className="section-title">Recent regulatory changes</div>
            {data.recent_changes.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No changes detected.</div>}
            {data.recent_changes.map((ch) => (
              <div key={ch.id} style={{ borderBottom: "1px solid var(--co-border)", padding: ".6rem 0" }}>
                <div className="work-row" style={{ borderBottom: "none", padding: 0 }}>
                  <span className={`dotsev ${ch.impact_level === "HIGH" ? "CRITICAL" : ch.impact_level === "MEDIUM" ? "MEDIUM" : "INFO"}`} />
                  <div className="grow">
                    <div className="title">{ch.title}</div>
                    <div className="meta">detected {fmt(ch.detected_at)} · {ch.status}</div>
                  </div>
                  <span className={`chip ${ch.impact_level === "HIGH" ? "CRITICAL" : ch.impact_level === "MEDIUM" ? "MEDIUM" : "INFO"}`}>{ch.impact_level}</span>
                  {ch.status !== "ASSESSED" && canManage && (
                    <button className="btn btn-sm btn-co" onClick={() => assess(ch.id)}>Assess impact</button>
                  )}
                </div>
                {ch.summary && <div className="muted" style={{ fontSize: ".82rem", paddingLeft: "1.4rem" }}>{ch.summary}</div>}
                {ch.assessment && (
                  <div style={{ paddingLeft: "1.4rem", marginTop: ".3rem", display: "flex", flexWrap: "wrap", gap: ".35rem" }}>
                    <span className="chip INFO">{ch.assessment.affected_requirement_ids.length} requirements</span>
                    <span className="chip INFO">{ch.assessment.affected_control_ids.length} controls</span>
                    <span className="chip INFO">{ch.assessment.affected_workflow_codes.length} workflows</span>
                    <span className="chip INFO">{ch.assessment.affected_customer_count} customers</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
        <div className="col-md-5">
          <div className="co-card">
            <div className="section-title">Control implementation</div>
            {Object.entries(data.control_status_counts).map(([s, n]) => (
              <div className="factor" key={s}>
                <span><span className={`chip ${CTRL_SEV[s] || "INFO"}`}>{s.replace(/_/g, " ")}</span></span>
                <b>{n}</b>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Obligation -> control -> software matrix (Authority → Source → Requirement → Control) */}
      {sources.map((s) => (
        <div className="co-card" key={s.id}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div className="section-title" style={{ marginBottom: 0 }}>
              {s.authority} · {s.name}
            </div>
            {s.official_url && <a href={s.official_url} target="_blank" rel="noreferrer" style={{ fontSize: ".8rem" }}>source ↗</a>}
          </div>
          <div style={{ overflowX: "auto", marginTop: ".4rem" }}>
            <table className="table table-sm align-middle" style={{ fontSize: ".88rem" }}>
              <thead><tr className="muted"><th>Article</th><th>Requirement</th><th>Software control</th><th>Status</th></tr></thead>
              <tbody>
                {(s.requirements || []).map((r) => {
                  const c = (controlsByReq[r.id] || [])[0];
                  return (
                    <tr key={r.id}>
                      <td className="muted">{r.article_reference}</td>
                      <td>{r.title}</td>
                      <td>{c ? <span>{c.name} <span className="muted">· {c.software_module}</span></span> : <span className="muted">—</span>}</td>
                      <td>{c ? <span className={`chip ${CTRL_SEV[c.implementation_status] || "INFO"}`}>{c.implementation_status.replace(/_/g, " ")}</span> : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </>
  );
};
