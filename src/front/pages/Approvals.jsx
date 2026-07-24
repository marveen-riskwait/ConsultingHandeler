import { useEffect, useState, useCallback } from "react";
import { api } from "../services/api";
import { can } from "../permissions/can";
import useGlobalReducer from "../hooks/useGlobalReducer";

const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : "—");
const ACTION_LABELS = { CUSTOMER_DELETE: "Delete customer" };
const STATUS_SEV = {
  PENDING: "MEDIUM", EXECUTED: "LOW", APPROVED: "LOW",
  REJECTED: "INFO", FAILED: "CRITICAL",
};

// The maker-checker queue: acts placed under dual control wait here for a
// second person to approve or reject. Four-eyes is enforced server-side (you
// cannot approve a request you made) — the UI surfaces the 403.
export const Approvals = () => {
  const { store } = useGlobalReducer();
  const me = store.user;
  const [view, setView] = useState("PENDING");
  const [rows, setRows] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [rejecting, setRejecting] = useState(null);
  const [reason, setReason] = useState("");

  const load = useCallback(() => {
    api.dualControl(view).then(setRows).catch((e) => setError(e.message));
  }, [view]);
  useEffect(() => { load(); }, [load]);

  const act = (fn, ok) => {
    setError(null); setNotice(null);
    return fn().then(() => { setNotice(ok); load(); }).catch((e) => setError(e.message));
  };
  const canApprove = can(me, "dualcontrol.approve");

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: ".6rem", marginBottom: "1rem" }}>
        <h3 style={{ margin: 0 }}>Approvals</h3>
        <div className="pt-tabs" style={{ margin: 0 }}>
          {["PENDING", "ALL"].map((v) => (
            <button key={v} className={"pt-tab" + (view === v ? " active" : "")}
              onClick={() => setView(v)}>{v === "PENDING" ? "Awaiting approval" : "All"}</button>
          ))}
        </div>
      </div>

      {error && <div className="alert alert-danger py-2">{error}</div>}
      {notice && <div className="alert alert-success py-2">{notice}</div>}

      <div className="co-card">
        <div className="section-title">
          Dual-control requests {rows.length > 0 && `(${rows.length})`}
        </div>
        {rows.length === 0 && (
          <div className="empty">Nothing waiting for a second approval. 🎉</div>
        )}
        <div className="co-rows">
        {rows.map((r) => (
          <div key={r.id} style={{ borderBottom: "1px solid var(--co-border)", padding: ".7rem 0" }}>
            <div className="work-row" style={{ borderBottom: "none", padding: 0 }}>
              <span className={`dotsev ${STATUS_SEV[r.status] || "INFO"}`} />
              <div className="grow">
                <div className="title">
                  {ACTION_LABELS[r.action_type] || r.action_type}
                  {r.summary ? ` — ${r.summary}` : ""}
                </div>
                <div className="meta">
                  requested by <b>{r.requested_by_name || "—"}</b> · {fmt(r.created_at)}
                  {r.decided_by_name ? ` · decided by ${r.decided_by_name}` : ""}
                </div>
                {r.reason && <div className="meta">Reason: {r.reason}</div>}
                {r.rejection_reason && (
                  <div className="meta" style={{ color: "var(--sev-high)" }}>Rejected: {r.rejection_reason}</div>
                )}
                {r.result_note && <div className="meta">{r.result_note}</div>}
              </div>
              <span className={`chip ${STATUS_SEV[r.status] || "INFO"}`}>{r.status}</span>
              {r.status === "PENDING" && canApprove && (
                <>
                  <button className="btn btn-sm btn-outline-success"
                    onClick={() => act(() => api.approveDualControl(r.id), "Approved and executed.")}>
                    Approve
                  </button>
                  <button className="btn btn-sm btn-outline-danger"
                    onClick={() => { setRejecting(rejecting === r.id ? null : r.id); setReason(""); }}>
                    Reject
                  </button>
                </>
              )}
            </div>
            {rejecting === r.id && (
              <div className="wf-complete" style={{ marginLeft: "1.4rem" }}>
                <input className="form-control form-control-sm" placeholder="Why reject? (audited)"
                  value={reason} autoFocus onChange={(e) => setReason(e.target.value)} />
                <div className="d-flex gap-2" style={{ marginTop: ".4rem" }}>
                  <button className="btn btn-sm btn-outline-secondary" onClick={() => setRejecting(null)}>Cancel</button>
                  <button className="btn btn-sm btn-danger" disabled={reason.trim().length < 3}
                    onClick={() => { act(() => api.rejectDualControl(r.id, reason.trim()), "Rejected."); setRejecting(null); }}>
                    Send back
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
        </div>
      </div>
    </>
  );
};
