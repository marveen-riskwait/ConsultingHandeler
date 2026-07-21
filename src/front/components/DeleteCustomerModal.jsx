import { useEffect, useState } from "react";
import { api } from "../services/api";

// Removing a customer has two very different meanings, so the modal makes the
// safe one the default: the record leaves the working set (archived, fully
// recoverable). Erasing the rows underneath is opt-in via "Delete from the
// database" — it needs customer.delete, the exact name, an audited reason, and
// it still refuses when AML retention rules apply.
export const DeleteCustomerModal = ({ customer, onClose, onDeleted, onArchived }) => {
  const [check, setCheck] = useState(null);
  const [purge, setPurge] = useState(false);
  const [confirmName, setConfirmName] = useState("");
  const [reason, setReason] = useState("");
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.deletionCheck(customer.id).then((c) => {
      setCheck(c);
      // Archiving an already-archived file is a no-op, so from the archive view
      // the only meaningful action is the destructive one — start there.
      if (c.customer?.status === "ARCHIVED" && c.can_delete) setPurge(true);
    }).catch((e) => setError(e.message));
  }, [customer.id]);

  const alreadyArchived = check?.customer?.status === "ARCHIVED";
  const blocked = (check?.blockers || []).length > 0;
  const canDelete = !!check?.can_delete;
  const canOverride = !!check?.can_override;
  const nameOk = confirmName.trim().toLowerCase() === customer.name.trim().toLowerCase();
  const reasonOk = reason.trim().length >= 5;

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      if (purge) {
        await api.deleteCustomer(customer.id, {
          reason: reason.trim(), confirm_name: confirmName.trim(),
          force: blocked && force,
        });
        onDeleted();
      } else {
        await api.archiveCustomer(customer.id, reason.trim() || "Archived by user");
        onArchived();
      }
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const blocksPurge = purge && (!nameOk || !reasonOk || (blocked && !(canOverride && force)));
  const c = check?.counts || {};

  return (
    <div className="cd-backdrop" onClick={onClose}>
      <div className="co-card cd-modal" onClick={(e) => e.stopPropagation()}>
        <h4 style={{ marginTop: 0 }}>
          <i className={purge ? "fa-solid fa-triangle-exclamation" : "fa-solid fa-box-archive"}
            style={{ color: purge ? "var(--sev-critical)" : "var(--accent)" }} />{" "}
          Remove “{customer.name}”?
        </h4>
        <p className="muted" style={{ fontSize: ".85rem" }}>
          {purge
            ? <>The customer and its dependent records are <b>erased permanently</b>.
              Only the audit trail survives — who deleted it, when and why.</>
            : alreadyArchived
              ? <>This customer is <b>already archived</b>. Restore it from the
                archive list, or tick the box below to erase it for good.</>
              : <>The customer leaves your active workspace and is kept as an
                archived file. Nothing is destroyed, and it can be restored.</>}
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

            <label className="cd-label">Reason (audited)</label>
            <input className="form-control form-control-sm" value={reason}
              placeholder="e.g. duplicate created by mistake"
              onChange={(e) => setReason(e.target.value)} />

            {canDelete && (
              <label className="cd-force" style={{ marginTop: ".8rem" }}>
                <input type="checkbox" checked={purge}
                  onChange={(e) => setPurge(e.target.checked)} />{" "}
                <b>Delete from the database</b> — erase the underlying records
                too, not just remove them from the workspace.
              </label>
            )}

            {purge && blocked && (
              <div className="alert alert-warning py-2" style={{ marginTop: ".7rem" }}>
                <b>Retention rules apply — archiving is the correct action:</b>
                <ul style={{ margin: ".3rem 0 0 1rem" }}>
                  {check.blockers.map((b) => <li key={b}>{b}</li>)}
                </ul>
              </div>
            )}

            {purge && (
              <>
                <label className="cd-label">
                  Type <b>{customer.name}</b> to confirm
                </label>
                <input className="form-control form-control-sm" value={confirmName}
                  onChange={(e) => setConfirmName(e.target.value)} />
              </>
            )}

            {purge && blocked && canOverride && (
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
              <button className={"btn btn-sm " + (purge ? "btn-danger" : "btn-co")}
                onClick={submit} disabled={busy || blocksPurge || (!purge && alreadyArchived)}>
                {purge
                  ? <><i className="fa-solid fa-trash" /> {busy ? "Deleting…" : "Delete permanently"}</>
                  : <><i className="fa-solid fa-box-archive" /> {busy ? "Removing…" : "Remove from workspace"}</>}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
