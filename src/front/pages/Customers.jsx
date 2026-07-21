import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { can } from "../permissions/can";
import { DeleteCustomerModal } from "../components/DeleteCustomerModal";
import { RowMenu } from "../components/RowMenu";
import { NameSuggest } from "../components/NameSuggest";

const EMPTY_FORM = {
  name: "", customer_type: "INDIVIDUAL", country: "",
  business_activity: "", complex_ownership: false,
};

export const Customers = () => {
  const { store } = useGlobalReducer();
  const canCreate = can(store.user, "customer.create");
  // Removing is archiving by default, so it follows customer.update; erasing
  // the rows underneath is what customer.delete gates, inside the modal.
  const canRemove = can(store.user, "customer.update");
  const [deleting, setDeleting] = useState(null);
  const [archived, setArchived] = useState(false);
  const [customers, setCustomers] = useState([]);
  const [error, setError] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);

  const load = () => api.customers(archived).then(setCustomers).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [archived]);

  const restore = async (cu) => {
    try { await api.restoreCustomer(cu.id, "Restored from archive"); load(); }
    catch (e) { setError(e.message); }
  };

  const create = async (e) => {
    e.preventDefault();
    if (creating) return;                    // no double submit on a slow POST
    setCreating(true); setError(null);
    try {
      const created = await api.createCustomer(form);
      // Show the new file straight away from the POST response, then reconcile
      // with the server (risk scoring runs on create, so the row is refreshed).
      setCustomers((prev) => [created, ...prev.filter((c) => c.id !== created.id)]);
      setForm(EMPTY_FORM);
      setShowForm(false);
      setNotice(`Customer registered — ${created.name}.`);
      load();
    } catch (err) { setError(err.message); }
    finally { setCreating(false); }
  };

  // Let the confirmation fade instead of lingering over the list.
  useEffect(() => {
    if (!notice) return undefined;
    const t = setTimeout(() => setNotice(null), 5000);
    return () => clearTimeout(t);
  }, [notice]);

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h3 style={{ margin: 0 }}>{archived ? "Archived customers" : "Customers"}</h3>
        <div style={{ display: "flex", gap: ".5rem" }}>
          <button className="btn btn-outline-secondary"
            onClick={() => setArchived((a) => !a)}
            title="Archived files are kept but taken out of the active book">
            <i className="fa-solid fa-box-archive" />{" "}
            {archived ? "Back to active" : "Archived"}
          </button>
          {canCreate && !archived && (
            <button className="btn btn-co" onClick={() => setShowForm((s) => !s)}>
              <i className="fa-solid fa-plus" /> New customer
            </button>
          )}
        </div>
      </div>

      {error && <div className="alert alert-danger">{error}</div>}
      {notice && (
        <div className="alert alert-success py-2">
          <i className="fa-solid fa-circle-check" /> {notice}
        </div>
      )}

      {showForm && (
        <form className="co-card" onSubmit={create} style={{ marginBottom: "1rem" }}>
          <div className="row g-2">
            <div className="col-md-6">
              <label className="form-label">Name</label>
              <NameSuggest value={form.name}
                placeholder="Start typing — existing files and watchlists appear"
                onChange={(name) => setForm({ ...form, name })} />
            </div>
            <div className="col-md-3">
              <label className="form-label">Type</label>
              <select className="form-select" value={form.customer_type}
                onChange={(e) => setForm({ ...form, customer_type: e.target.value })}>
                <option value="INDIVIDUAL">Individual</option>
                <option value="COMPANY">Company</option>
              </select>
            </div>
            <div className="col-md-3">
              <label className="form-label">Country</label>
              <input className="form-control" value={form.country}
                onChange={(e) => setForm({ ...form, country: e.target.value })} />
            </div>
            <div className="col-md-6">
              <label className="form-label">Business activity</label>
              <input className="form-control" value={form.business_activity} placeholder="e.g. crypto exchange"
                onChange={(e) => setForm({ ...form, business_activity: e.target.value })} />
            </div>
            <div className="col-md-6 d-flex align-items-end">
              <div className="form-check">
                <input className="form-check-input" type="checkbox" checked={form.complex_ownership}
                  onChange={(e) => setForm({ ...form, complex_ownership: e.target.checked })} id="cx" />
                <label className="form-check-label" htmlFor="cx">Complex ownership structure</label>
              </div>
            </div>
          </div>
          <button className="btn btn-co mt-3" disabled={creating}>
            {creating ? "Creating…" : "Create"}
          </button>
        </form>
      )}

      <div className="co-card">
        {customers.length === 0 && (
          <div className="empty">
            {archived ? "No archived customers." : "No customers yet."}
          </div>
        )}
        {customers.map((cu) => (
          <div className="work-row" key={cu.id}>
            <span className={`dotsev ${cu.risk_level}`} />
            <div className="grow">
              <div className="title"><Link to={`/customers/${cu.id}`}>{cu.name}</Link></div>
              <div className="meta">
                {cu.customer_type} · {cu.country || "—"}
                {cu.business_activity ? ` · ${cu.business_activity}` : ""}
                {cu.is_pep ? " · PEP" : ""}
                {cu.has_sanctions_match ? " · SANCTIONS" : ""}
              </div>
            </div>
            <span className={`chip ${cu.risk_level}`}>{cu.risk_level} · {cu.risk_score}</span>
            <Link to={`/customers/${cu.id}`} className="btn btn-sm btn-outline-secondary">Open</Link>
            <RowMenu items={[
              archived && canRemove && {
                label: "Restore to active", icon: "fa-solid fa-rotate-left",
                onClick: () => restore(cu),
              },
              canRemove && {
                label: archived ? "Delete record…" : "Remove customer…",
                icon: "fa-solid fa-trash", danger: true,
                onClick: () => setDeleting(cu),
              },
            ]} />
          </div>
        ))}
      </div>

      {deleting && (
        <DeleteCustomerModal
          customer={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => { setDeleting(null); load(); }}
          onArchived={() => { setDeleting(null); load(); }}
        />
      )}
    </>
  );
};
