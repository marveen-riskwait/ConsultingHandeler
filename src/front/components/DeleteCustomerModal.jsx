import { useEffect, useState } from "react";
import { api } from "../services/api";

// Deleting a customer is irreversible and touches compliance records, so the
// modal shows exactly what will be erased, refuses when retention rules apply
// (offering Archive instead), and requires the exact name + an audited reason.
export const DeleteCustomerModal = ({ customer, canOverride, onClose, onDeleted, onArchived }) => {
  const [check, setCheck] = useState(null);
  const [confirmName, setConfirmName] = useState("");
  const [reason, setReason] = useState("");
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.deletionCheck(customer.id).then(setCheck).catch((e) => setError(e.message));
  }, [customer.id]);

  const blocked = (check?.blockers || []).length > 0;
  const nameOk = confirmName.trim().toLowerCase() === customer.name.trim().toLowerCase();
  const reasonOk = reason.trim().length >= 5;

  const doDelete = async () => {
    setBusy(true); setError(null);
    try {
      await api.deleteCustomer(customer.id, {
        reason: reason.trim(), confirm_name: confirmName.trim(),
        force: blocked && force,
      });
      onDeleted();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const doArchive = async () => {
    setBusy(true); setError(null);
    try {
      await api.archiveCustomer(customer.id, reason.trim() || "Archived by user");
      onArchived();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const c = check?.counts || {};
  return (
    <div className="cd-backdrop" onClick={onClose}>
      <div className="co-card cd-modal" onClick={(e) => e.stopPropagation()}>
        <h4 style={{ marginTop: 0 }}>
          <i className="fa-solid fa-triangle-exclamation" style={{ color: "var(--sev-critical)" }} />{" "}
          Delete “{customer.name}”?
        </h4>
        <p className="muted" style={{ fontSize: ".85rem" }}>
          This is permanent. The customer and its dependent records are erased —
          only the <b>audit trail</b> survives (who deleted it, when and why).
        </p>

        {error && <div className="alert alert-danger py-2">{error}</div>}
        {!check && !error && <div className="empty">Checking records…</div>}

        {check && (
          <>
            <div className="cd-counts">
              {[["cases", "cases"], ["tasks", "tasks"], ["documents", "documents"],
                ["screening_matches", "screening matches"], ["alerts", "alerts"],
                ["events", "events"]].map(([k, label]) => (
                <span key={k} className={`chip ${c[k] ? "MEDIUM" : "INFO"}`}>
                  {c[k] || 0} {label}
                </span>
              ))}
            </div>

            {blocked && (
              <div className="alert alert-warning py-2" style={{ marginTop: ".7rem" }}>
                <b>Retention rules apply — archiving is the correct action:</b>
                <ul style={{ margin: ".3rem 0 0 1rem" }}>
                  {check.blockers.map((b) => <li key={b}>{b}</li>)}
                </ul>
              </div>
            )}

            <label className="cd-label">Reason (audited)</label>
            <input className="form-control form-control-sm" value={reason}
              placeholder="e.g. duplicate created by mistake"
              onChange={(e) => setReason(e.target.value)} />

            <label className="cd-label">
              Type <b>{customer.name}</b> to confirm
            </label>
            <input className="form-control form-control-sm" value={confirmName}
              onChange={(e) => setConfirmName(e.target.value)} />

            {blocked && canOverride && (
              <label className="cd-force">
                <input type="checkbox" checked={force}
                  onChange={(e) => setForce(e.target.checked)} />{" "}
                I am an administrator and I accept overriding the retention
                rules above.
              </label>
            )}

            <div className="cd-actions">
              <button className="btn btn-sm btn-outline-secondary" onClick={onClose}
                disabled={busy}>Cancel</button>
              <button className="btn btn-sm btn-outline-primary" onClick={doArchive}
                disabled={busy}>
                <i className="fa-solid fa-box-archive" /> Archive instead
              </button>
              <button className="btn btn-sm btn-danger" onClick={doDelete}
                disabled={busy || !nameOk || !reasonOk || (blocked && !(canOverride && force))}>
                <i className="fa-solid fa-trash" /> {busy ? "Deleting…" : "Delete permanently"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
