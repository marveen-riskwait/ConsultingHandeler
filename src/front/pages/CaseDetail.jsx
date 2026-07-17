import { useEffect, useState, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";
import { can } from "../permissions/can";

const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : "—");

export const CaseDetail = () => {
  const { id } = useParams();
  const { store } = useGlobalReducer();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.case(id).then(setData).catch((e) => setError(e.message)), [id]);
  useEffect(() => { load(); }, [load]);

  const decide = async (decision) => {
    if (!reason.trim()) { setError("A reason is required for every decision."); return; }
    setBusy(true); setError(null);
    try { await api.decideCase(id, decision, reason); setReason(""); await load(); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  if (error && !data) return <div className="alert alert-danger">{error}</div>;
  if (!data) return <div className="empty">Loading case…</div>;

  const { case: cs, customer, risk, related_events, audit } = data;
  const closed = cs.status === "CLOSED";
  const canConfirm = can(store.user, "screening.confirm");
  const sanctionsMatch = related_events.find((e) => e.event_type === "SANCTIONS_MATCH_FOUND");

  return (
    <>
      <div className="muted" style={{ fontSize: ".8rem" }}>
        <Link to={`/customers/${customer.id}`}>← {customer.name}</Link>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: ".3rem 0 1rem" }}>
        <h3 style={{ margin: 0 }}>{cs.title}</h3>
        <div style={{ display: "flex", gap: ".5rem" }}>
          <span className={`chip ${cs.priority}`}>{cs.priority}</span>
          <span className="chip INFO">{cs.status}</span>
        </div>
      </div>

      <div className="row g-3">
        <div className="col-md-7">
          {/* Sanctions comparison, when relevant */}
          {sanctionsMatch && (
            <div className="co-card">
              <div className="section-title">Potential match — compare</div>
              <div className="row">
                <div className="col-6">
                  <div className="muted" style={{ fontSize: ".78rem" }}>Customer</div>
                  <div><b>{customer.name}</b></div>
                  <div className="muted" style={{ fontSize: ".85rem" }}>Country: {customer.country || "—"}</div>
                </div>
                <div className="col-6">
                  <div className="muted" style={{ fontSize: ".78rem" }}>Sanctions record</div>
                  <div><b>{sanctionsMatch.payload.matched_name}</b></div>
                  <div className="muted" style={{ fontSize: ".85rem" }}>DOB: {sanctionsMatch.payload.dob || "Unknown"}</div>
                  <div className="muted" style={{ fontSize: ".85rem" }}>Nationality: {sanctionsMatch.payload.nationality || "Unknown"}</div>
                </div>
              </div>
              <div className="mt-2">
                <span className="chip HIGH">Match confidence {sanctionsMatch.payload.match_score}%</span>{" "}
                <span className="muted" style={{ fontSize: ".8rem" }}>Source: {sanctionsMatch.source}</span>
              </div>
              <p className="muted mt-2 mb-0" style={{ fontSize: ".82rem" }}>
                A name match is not a confirmed hit — compare identity attributes before deciding.
              </p>
            </div>
          )}

          {/* Tasks */}
          <div className="co-card">
            <div className="section-title">Investigation tasks</div>
            {(cs.tasks || []).length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No tasks.</div>}
            {(cs.tasks || []).map((t) => (
              <div className="work-row" key={t.id}>
                <span className={`dotsev ${t.priority}`} />
                <div className="grow"><div className="title">{t.title}</div><div className="meta">{t.status}</div></div>
                <span className={`chip ${t.status === "DONE" ? "LOW" : t.priority}`}>{t.status}</span>
              </div>
            ))}
          </div>

          {/* Audit trail */}
          <div className="co-card">
            <div className="section-title">Audit trail</div>
            {audit.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No entries.</div>}
            {audit.map((a) => (
              <div className="tl-item" key={a.id}>
                <span className="when">{fmt(a.created_at)}</span>
                <span style={{ fontSize: ".85rem" }}>
                  <b>{a.action}</b> by {a.actor_label}
                  {a.old_value || a.new_value ? ` — ${a.old_value || ""} → ${a.new_value || ""}` : ""}
                  {a.reason ? <span className="muted"> ({a.reason})</span> : null}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Decision panel */}
        <div className="col-md-5">
          <div className="co-card">
            <div className="section-title">Risk context</div>
            <div style={{ display: "flex", alignItems: "baseline", gap: ".5rem" }}>
              <span className="risk-badge">{customer.risk_score}<small>/100</small></span>
              <span className={`chip ${customer.risk_level}`}>{customer.risk_level}</span>
            </div>
            {risk && (risk.factors || []).map((f) => (
              <div className="factor" key={f.code}><span>{f.label}</span><span className="impact">+{f.impact}</span></div>
            ))}
          </div>

          <div className="co-card">
            <div className="section-title">Decision</div>
            {closed ? (
              <div className="alert alert-secondary mb-0" style={{ fontSize: ".88rem" }}>
                <b>{cs.decision}</b><br />
                <span className="muted">{cs.decision_reason}</span>
              </div>
            ) : (
              <>
                {error && <div className="alert alert-danger py-2" style={{ fontSize: ".85rem" }}>{error}</div>}
                <label className="form-label">Reason (required — kept in the audit trail)</label>
                <textarea className="form-control" rows="3" value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="e.g. Date of birth and nationality do not match. False positive." />
                <div className="d-grid gap-2 mt-3">
                  <button className="btn btn-outline-success" disabled={busy} onClick={() => decide("FALSE_POSITIVE")}>
                    <i className="fa-solid fa-check" /> False positive
                  </button>
                  <button className="btn btn-outline-danger" disabled={busy || !canConfirm}
                    title={canConfirm ? "" : "Requires screening.confirm permission"} onClick={() => decide("CONFIRMED_MATCH")}>
                    <i className="fa-solid fa-triangle-exclamation" /> Confirm match {canConfirm ? "" : "(no permission)"}
                  </button>
                  <button className="btn btn-outline-warning" disabled={busy} onClick={() => decide("ESCALATE")}>
                    <i className="fa-solid fa-arrow-up" /> Escalate
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
};
