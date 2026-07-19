import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";

const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : "—");

// The Auditor workspace: can every important decision be reconstructed?
export const Audit = () => {
  const { store } = useGlobalReducer();
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState(null);
  const [filters, setFilters] = useState({ entity_type: "", action: "" });

  const load = useCallback(() => {
    api.audit(filters).then(setEntries).catch((e) => setError(e.message));
  }, [filters]);
  useEffect(() => { load(); }, [load]);

  return (
    <>
      <h3 style={{ marginBottom: "1rem" }}>Audit Trail</h3>
      {error && <div className="alert alert-danger py-2">{error}</div>}

      <div className="co-card" style={{ marginBottom: "1rem" }}>
        <div className="row g-2">
          <div className="col-md-4">
            <label className="form-label">Entity type</label>
            <input className="form-control form-control-sm" placeholder="customer, case, review…"
              value={filters.entity_type}
              onChange={(e) => setFilters({ ...filters, entity_type: e.target.value })} />
          </div>
          <div className="col-md-4">
            <label className="form-label">Action contains</label>
            <input className="form-control form-control-sm" placeholder="RISK, DECISION, APPROVAL…"
              value={filters.action}
              onChange={(e) => setFilters({ ...filters, action: e.target.value })} />
          </div>
        </div>
      </div>

      <div className="co-card">
        <div className="section-title">{entries.length} entries (most recent first)</div>
        <div style={{ overflowX: "auto" }}>
          <table className="table table-sm align-middle" style={{ fontSize: ".85rem" }}>
            <thead>
              <tr className="muted">
                <th>When</th><th>Actor</th><th>Action</th><th>Entity</th>
                <th>Change</th><th>Reason</th><th>IP</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((a) => (
                <tr key={a.id}>
                  <td className="muted" style={{ whiteSpace: "nowrap" }}>{fmt(a.created_at)}</td>
                  <td>{a.actor_label}</td>
                  <td><b>{a.action}</b></td>
                  <td className="muted">
                    {a.entity_type}
                    {a.entity_id != null && a.entity_type === "customer"
                      ? <> <Link to={`/customers/${a.entity_id}`}>#{a.entity_id}</Link></>
                      : a.entity_id != null ? ` #${a.entity_id}` : ""}
                  </td>
                  <td>{(a.old_value || a.new_value)
                    ? <span className="muted">{a.old_value || "∅"} → {a.new_value || "∅"}</span> : "—"}</td>
                  <td className="muted">{a.reason || "—"}</td>
                  <td className="muted">{a.ip_address || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
};
