import { useEffect, useState, useCallback } from "react";
import { api } from "../services/api";
import { can } from "../permissions/can";
import useGlobalReducer from "../hooks/useGlobalReducer";

// The suspicious-activity-report flow for a case: draft -> four-eyes approval
// -> filed, with a goAML export. Four-eyes is enforced server-side (the author
// cannot approve their own report); the UI simply surfaces the 403.
const STATUS_SEV = {
  DRAFT: "INFO", PENDING_APPROVAL: "MEDIUM", APPROVED: "LOW",
  SUBMITTED: "LOW", REJECTED: "HIGH",
};

export const SarPanel = ({ customerId, caseId, indicators }) => {
  const { store } = useGlobalReducer();
  const me = store.user;
  const [sars, setSars] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [drafting, setDrafting] = useState(false);
  const [form, setForm] = useState({ report_type: "STR", reason: "" });
  const [rejecting, setRejecting] = useState(null);
  const [rejectReason, setRejectReason] = useState("");

  const load = useCallback(() => {
    api.sars().then((all) => setSars(all.filter((s) => s.case_id === caseId)))
      .catch((e) => setError(e.message));
  }, [caseId]);
  useEffect(() => { load(); }, [load]);

  const act = async (fn, ok) => {
    setError(null); setNotice(null);
    try { await fn(); if (ok) setNotice(ok); load(); }
    catch (e) { setError(e.message); }
  };

  const createDraft = () => act(async () => {
    await api.createSar(customerId, {
      report_type: form.report_type, reason: form.reason.trim(),
      indicators: indicators || [], case_id: caseId });
    setDrafting(false); setForm({ report_type: "STR", reason: "" });
  }, "SAR drafted.");

  if (!can(me, "sar.view")) return null;

  return (
    <div className="co-card">
      <div className="section-title">Suspicious activity report</div>
      {error && <div className="alert alert-danger py-2" style={{ fontSize: ".85rem" }}>{error}</div>}
      {notice && <div className="alert alert-success py-2" style={{ fontSize: ".85rem" }}>{notice}</div>}

      {sars.length === 0 && !drafting && (
        <div className="muted" style={{ fontSize: ".85rem" }}>
          No report filed on this case yet.
        </div>
      )}

      {sars.map((s) => (
        <div key={s.id} style={{ borderTop: "1px solid var(--co-border)", paddingTop: ".6rem", marginTop: ".6rem" }}>
          <div className="d-flex align-items-center gap-2">
            <b>{s.reference}</b>
            <span className="muted" style={{ fontSize: ".8rem" }}>{s.report_type}</span>
            <span className={`chip ${STATUS_SEV[s.status] || "INFO"}`}>{s.status.replace(/_/g, " ")}</span>
          </div>
          <div className="meta" style={{ marginTop: ".2rem" }}>
            drafted by {s.created_by_name || "—"}
            {s.approved_by_name ? ` · approved by ${s.approved_by_name}` : ""}
            {s.submitted_at ? ` · filed ${new Date(s.submitted_at).toLocaleDateString()}` : ""}
          </div>
          {s.reason && <div className="wf-note" style={{ marginTop: ".3rem" }}>{s.reason}</div>}
          {s.rejection_reason && (
            <div className="meta" style={{ color: "var(--sev-high)" }}>Returned: {s.rejection_reason}</div>
          )}

          <div className="d-flex gap-2 flex-wrap" style={{ marginTop: ".5rem" }}>
            {["DRAFT", "REJECTED"].includes(s.status) && can(me, "sar.create") && (
              <button className="btn btn-sm btn-co"
                onClick={() => act(() => api.submitSarForApproval(s.id), "Sent for approval.")}>
                Submit for approval
              </button>
            )}
            {s.status === "PENDING_APPROVAL" && can(me, "sar.approve") && (
              <>
                <button className="btn btn-sm btn-outline-success"
                  onClick={() => act(() => api.approveSar(s.id), "Approved.")}>Approve</button>
                <button className="btn btn-sm btn-outline-danger"
                  onClick={() => { setRejecting(rejecting === s.id ? null : s.id); setRejectReason(""); }}>
                  Reject
                </button>
              </>
            )}
            {s.status === "APPROVED" && can(me, "sar.submit") && (
              <button className="btn btn-sm btn-co"
                onClick={() => act(() => api.markSarSubmitted(s.id), "Marked as filed with the FIU.")}>
                Mark as filed
              </button>
            )}
            {["APPROVED", "SUBMITTED"].includes(s.status) && (
              <a className="btn btn-sm btn-outline-secondary" href={api.sarExportUrl(s.id)}>
                <i className="fa-solid fa-download" /> goAML XML
              </a>
            )}
          </div>

          {rejecting === s.id && (
            <div className="wf-complete" style={{ marginTop: ".45rem" }}>
              <input className="form-control form-control-sm" placeholder="Why is it sent back? (audited)"
                value={rejectReason} autoFocus onChange={(e) => setRejectReason(e.target.value)} />
              <div className="d-flex gap-2" style={{ marginTop: ".4rem" }}>
                <button className="btn btn-sm btn-outline-secondary" onClick={() => setRejecting(null)}>Cancel</button>
                <button className="btn btn-sm btn-danger" disabled={rejectReason.trim().length < 3}
                  onClick={() => { act(() => api.rejectSar(s.id, rejectReason.trim()), "Returned to the author."); setRejecting(null); }}>
                  Send back
                </button>
              </div>
            </div>
          )}
        </div>
      ))}

      {drafting ? (
        <div style={{ marginTop: ".7rem" }}>
          <label className="cd-label">Report type</label>
          <select className="form-select form-select-sm" value={form.report_type}
            onChange={(e) => setForm({ ...form, report_type: e.target.value })}>
            <option value="STR">STR — suspicious transaction</option>
            <option value="SAR">SAR — suspicious activity</option>
          </select>
          <label className="cd-label mt-2">Grounds for suspicion (filed to the FIU)</label>
          <textarea className="form-control form-control-sm" rows={3} value={form.reason}
            placeholder="What is suspicious, and why…"
            onChange={(e) => setForm({ ...form, reason: e.target.value })} />
          <div className="d-flex gap-2" style={{ marginTop: ".45rem" }}>
            <button className="btn btn-sm btn-outline-secondary" onClick={() => setDrafting(false)}>Cancel</button>
            <button className="btn btn-sm btn-co" disabled={form.reason.trim().length < 5}
              onClick={createDraft}>Create draft</button>
          </div>
        </div>
      ) : can(me, "sar.create") && (
        <button className="btn btn-sm btn-outline-danger" style={{ marginTop: ".7rem" }}
          onClick={() => setDrafting(true)}>
          <i className="fa-solid fa-flag" /> Draft a report
        </button>
      )}
    </div>
  );
};
