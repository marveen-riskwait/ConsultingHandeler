import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";

const fmt = (iso) => (iso ? new Date(iso).toLocaleDateString() : "—");

// Data retention (storage limitation vs AML record-keeping): archived
// customers whose retention period has elapsed are erased here; the audit
// trail always survives.
export const Retention = () => {
  const [data, setData] = useState(null);
  const [months, setMonths] = useState("");
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(() => {
    api.retention().then((d) => { setData(d); setMonths(String(d.retention_months)); })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);

  const saveMonths = async (e) => {
    e.preventDefault(); setError(null); setNotice(null);
    try { await api.setRetentionPolicy(Number(months)); setNotice("Retention period saved."); load(); }
    catch (err) { setError(err.message); }
  };
  const purge = async () => {
    setError(null); setNotice(null); setConfirming(false);
    try {
      const r = await api.runRetentionPurge(false);
      setNotice(`Purged ${r.count} record(s) past retention. The audit trail is kept.`);
      load();
    } catch (err) { setError(err.message); }
  };

  if (!data) return <div className="empty">Loading…</div>;
  const candidates = data.candidates || [];

  return (
    <>
      <h3 style={{ marginBottom: "1rem" }}>Data retention</h3>
      {error && <div className="alert alert-danger py-2">{error}</div>}
      {notice && <div className="alert alert-success py-2">{notice}</div>}

      <div className="co-card" style={{ maxWidth: 520 }}>
        <div className="section-title">Retention period</div>
        <p className="muted" style={{ fontSize: ".85rem" }}>
          How long records are kept after a relationship ends (archived).
          AML record-keeping is typically 5 years (60 months).
        </p>
        <form onSubmit={saveMonths} className="d-flex gap-2 align-items-end">
          <div>
            <label className="form-label">Months</label>
            <input className="form-control" type="number" min="0" style={{ width: 120 }}
              value={months} onChange={(e) => setMonths(e.target.value)} />
          </div>
          <button className="btn btn-co">Save</button>
        </form>
      </div>

      <div className="co-card" style={{ marginTop: "1rem" }}>
        <div className="section-title">
          Due for erasure {candidates.length > 0 && `(${candidates.length})`}
        </div>
        {candidates.length === 0 && (
          <div className="muted" style={{ fontSize: ".88rem" }}>
            No archived record is past its retention period. 🎉
          </div>
        )}
        <div className="co-rows">
        {candidates.map((c) => (
          <div className="work-row" key={c.id}>
            <span className="dotsev MEDIUM" />
            <div className="grow">
              <div className="title"><Link to={`/customers/${c.id}`}>{c.name}</Link></div>
              <div className="meta">archived {fmt(c.archived_at)} · retention elapsed</div>
            </div>
          </div>
        ))}
        </div>
        {candidates.length > 0 && (
          confirming ? (
            <div className="wf-complete" style={{ marginTop: ".7rem" }}>
              <div className="meta" style={{ marginBottom: ".4rem" }}>
                Erase all {candidates.length} record(s) past retention? This is
                permanent — only the audit trail is kept.
              </div>
              <div className="d-flex gap-2">
                <button className="btn btn-sm btn-outline-secondary" onClick={() => setConfirming(false)}>Cancel</button>
                <button className="btn btn-sm btn-danger" onClick={purge}>Erase now</button>
              </div>
            </div>
          ) : (
            <button className="btn btn-outline-danger mt-3" onClick={() => setConfirming(true)}>
              <i className="fa-solid fa-trash" /> Run retention purge
            </button>
          )
        )}
      </div>
    </>
  );
};
