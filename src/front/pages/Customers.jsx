import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { can } from "../permissions/can";

export const Customers = () => {
  const { store } = useGlobalReducer();
  const canCreate = can(store.user, "customer.create");
  const [customers, setCustomers] = useState([]);
  const [error, setError] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", customer_type: "INDIVIDUAL", country: "", business_activity: "", complex_ownership: false });

  const load = () => api.customers().then(setCustomers).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  const create = async (e) => {
    e.preventDefault();
    try {
      await api.createCustomer(form);
      setForm({ name: "", customer_type: "INDIVIDUAL", country: "", business_activity: "", complex_ownership: false });
      setShowForm(false);
      load();
    } catch (err) { setError(err.message); }
  };

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h3 style={{ margin: 0 }}>Customers</h3>
        {canCreate && (
          <button className="btn btn-co" onClick={() => setShowForm((s) => !s)}>
            <i className="fa-solid fa-plus" /> New customer
          </button>
        )}
      </div>

      {error && <div className="alert alert-danger">{error}</div>}

      {showForm && (
        <form className="co-card" onSubmit={create} style={{ marginBottom: "1rem" }}>
          <div className="row g-2">
            <div className="col-md-6">
              <label className="form-label">Name</label>
              <input className="form-control" value={form.name} required
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
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
          <button className="btn btn-co mt-3">Create</button>
        </form>
      )}

      <div className="co-card">
        {customers.length === 0 && <div className="empty">No customers yet.</div>}
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
          </div>
        ))}
      </div>
    </>
  );
};
