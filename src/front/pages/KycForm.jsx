import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../services/api";
import { FilePreview } from "../components/FilePreview";

const fileSize = (bytes) => {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

// The full CDD questionnaire. Sections come from the backend schema (per
// customer type + risk rank — EDD sections appear automatically at HIGH+).
// Answers land in ProfileField with provenance; proofs land in Document; both
// feed the requirement engine, so the completeness bar moves as you fill.

const Field = ({ spec, value, onChange }) => {
  const v = value ?? "";
  const common = { id: `kf-${spec.key}` };
  let control;
  if (spec.type === "textarea") {
    control = <textarea {...common} className="form-control" rows={3} value={v}
      onChange={(e) => onChange(spec.key, e.target.value)} />;
  } else if (spec.type === "select") {
    control = (
      <select {...common} className="form-select" value={v}
        onChange={(e) => onChange(spec.key, e.target.value)}>
        <option value="">—</option>
        {spec.options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  } else if (spec.type === "multiselect") {
    const selected = v ? v.split(", ") : [];
    const toggle = (opt) => {
      const next = selected.includes(opt)
        ? selected.filter((s) => s !== opt)
        : [...selected, opt];
      onChange(spec.key, next.join(", "));
    };
    control = (
      <div className="kf-multi">
        {spec.options.map((o) => (
          <button type="button" key={o}
            className={"kf-chip" + (selected.includes(o) ? " on" : "")}
            onClick={() => toggle(o)}>
            {o}
          </button>
        ))}
      </div>
    );
  } else {
    const type = spec.type === "date" ? "date"
      : spec.type === "number" ? "number" : "text";
    control = <input {...common} type={type} className="form-control" value={v}
      onChange={(e) => onChange(spec.key, e.target.value)} />;
  }

  return (
    <div className="kf-field">
      <label htmlFor={common.id}>
        {spec.label}{spec.required && <span className="kf-req"> *</span>}
      </label>
      {control}
      {spec.help && <div className="kf-help">{spec.help}</div>}
    </div>
  );
};

export const KycForm = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [values, setValues] = useState({});
  const [dirty, setDirty] = useState({});
  const [active, setActive] = useState(null);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(null);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const load = useCallback(() => api.kycForm(id).then((d) => {
    setData(d);
    setValues(Object.fromEntries(
      Object.entries(d.values).map(([k, f]) => [k, f.value ?? ""])));
    setActive((a) => a || d.sections[0]?.key || "proofs");
  }).catch((e) => setError(e.message)), [id]);
  useEffect(() => { load(); }, [load]);

  if (error && !data) return <div className="alert alert-danger">{error}</div>;
  if (!data) return <div className="empty">Loading KYC form…</div>;

  const { customer, sections, proofs, completeness } = data;
  const onChange = (key, value) => {
    setValues((v) => ({ ...v, [key]: value }));
    setDirty((d) => ({ ...d, [key]: true }));
  };

  const sectionDone = (s) =>
    s.fields.every((f) => !f.required || (values[f.key] || "").trim() !== "");

  const saveSection = async (s) => {
    const payload = {};
    s.fields.forEach((f) => { if (dirty[f.key]) payload[f.key] = values[f.key]; });
    if (!Object.keys(payload).length) return true;
    setBusy(true); setError(null);
    try {
      const res = await api.saveKycForm(id, payload);
      setDirty((d) => {
        const next = { ...d };
        Object.keys(payload).forEach((k) => delete next[k]);
        return next;
      });
      setData((prev) => ({ ...prev, completeness: res.completeness }));
      setNotice(`Saved (${res.saved} field${res.saved === 1 ? "" : "s"}).`);
      return true;
    } catch (e) { setError(e.message); return false; }
    finally { setBusy(false); }
  };

  const uploadProof = async (docType, file) => {
    if (!file) return;
    setUploading(docType); setError(null);
    try {
      await api.uploadDocument(id, docType, file);
      await load();
      setNotice(`${file.name} uploaded.`);
    } catch (e) { setError(e.message); }
    finally { setUploading(null); }
  };

  const removeProof = async (doc) => {
    setBusy(true); setError(null);
    try {
      await api.deleteDocument(id, doc.id);
      await load();
      setNotice("Document removed.");
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const submit = async () => {
    // Flush any unsaved answers first, then finalize.
    for (const s of sections) {
      if (!(await saveSection(s))) return;
    }
    setBusy(true); setError(null);
    try {
      await api.submitKycForm(id);
      navigate(`/customers/${id}`);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const docsFor = (t) => data.documents.filter((d) => d.doc_type === t);
  const activeSection = sections.find((s) => s.key === active);

  return (
    <>
      <div className="d-flex justify-content-between align-items-start" style={{ marginBottom: "1rem" }}>
        <div>
          <div className="muted" style={{ fontSize: ".8rem" }}>
            <Link to={`/customers/${id}`}>← {customer.name}</Link>
          </div>
          <h3 style={{ margin: ".2rem 0" }}>KYC form</h3>
          <div className="muted" style={{ fontSize: ".85rem" }}>
            {customer.customer_type} · risk {customer.risk_level} — EDD sections
            appear automatically at HIGH risk and above.
          </div>
        </div>
        <button className="btn btn-co" onClick={submit} disabled={busy}>
          <i className="fa-solid fa-paper-plane" /> Submit for review
        </button>
      </div>

      <div className="co-card" style={{ marginBottom: "1rem", padding: ".85rem 1.25rem" }}>
        <div className="d-flex justify-content-between align-items-center">
          <span className="section-title" style={{ margin: 0 }}>Compliance completeness</span>
          <b>{completeness.completeness_pct}%</b>
        </div>
        <div className="progress" style={{ height: 8, marginTop: ".45rem" }}>
          <div className="progress-bar" role="progressbar"
            style={{ width: `${completeness.completeness_pct}%`, background: "var(--co-primary)" }} />
        </div>
      </div>

      {error && <div className="alert alert-danger py-2">{error}</div>}
      {notice && !error && (
        <div className="alert alert-success py-2" onAnimationEnd={() => setNotice(null)}>{notice}</div>
      )}

      <div className="kf-layout">
        <aside className="co-card kf-nav">
          {sections.map((s) => (
            <button key={s.key}
              className={"kf-nav-item" + (active === s.key ? " active" : "")}
              onClick={() => setActive(s.key)}>
              <i className={`fa-solid ${s.icon}`} />
              <span>{s.title}</span>
              {sectionDone(s) && <i className="fa-solid fa-circle-check kf-done" />}
            </button>
          ))}
          <button className={"kf-nav-item" + (active === "proofs" ? " active" : "")}
            onClick={() => setActive("proofs")}>
            <i className="fa-solid fa-file-shield" />
            <span>Proofs & documents</span>
          </button>
        </aside>

        <section className="co-card kf-panel">
          {activeSection && (
            <>
              <h4 style={{ marginTop: 0 }}>{activeSection.title}</h4>
              <p className="muted" style={{ fontSize: ".86rem" }}>{activeSection.description}</p>
              <div className="kf-grid">
                {activeSection.fields.map((f) => (
                  <Field key={f.key} spec={f} value={values[f.key]} onChange={onChange} />
                ))}
              </div>
              <button className="btn btn-co" style={{ marginTop: "1rem" }}
                onClick={() => saveSection(activeSection)} disabled={busy}>
                Save section
              </button>
            </>
          )}

          {active === "proofs" && (
            <>
              <h4 style={{ marginTop: 0 }}>Proofs &amp; documents</h4>
              <p className="muted" style={{ fontSize: ".86rem" }}>
                Evidence supporting the declarations: identity, address, income /
                source of funds, and corporate records.
              </p>
              {proofs.map((p) => {
                const existing = docsFor(p.doc_type);
                const received = existing.filter((d) => d.file_url);
                return (
                  <div className="work-row" key={p.doc_type}>
                    <span className={`dotsev ${received.length ? "LOW" : "HIGH"}`} />
                    <div className="grow">
                      <div className="title">{p.label}</div>
                      <div className="meta">{p.examples}</div>
                      {existing.map((d) => (
                        <div className="meta kf-doc" key={d.id}>
                          {d.file_url ? (
                            <>
                              <button type="button" className="kf-doc-link"
                                onClick={() => setPreview(d)}>
                                <i className="fa-solid fa-file-lines" /> {d.file_name}
                              </button>
                              <span> · {fileSize(d.file_size)} · {d.status}</span>
                            </>
                          ) : (
                            <><i className="fa-regular fa-hourglass" /> awaiting file — {d.status}</>
                          )}
                          <button type="button" className="kf-doc-remove"
                            title="Remove this document" onClick={() => removeProof(d)}>
                            <i className="fa-solid fa-xmark" />
                          </button>
                        </div>
                      ))}
                    </div>
                    <label className={"btn btn-sm btn-outline-secondary" +
                      (uploading === p.doc_type ? " disabled" : "")}>
                      <i className="fa-solid fa-arrow-up-from-bracket" />{" "}
                      {uploading === p.doc_type ? "Uploading…" : "Upload"}
                      <input type="file" hidden
                        accept=".pdf,.png,.jpg,.jpeg,.heic,.webp,.tif,.tiff"
                        disabled={uploading === p.doc_type}
                        onChange={(e) => {
                          uploadProof(p.doc_type, e.target.files?.[0]);
                          e.target.value = "";   // same file twice must re-fire
                        }} />
                    </label>
                  </div>
                );
              })}
            </>
          )}
        </section>
      </div>

      {preview && (
        <FilePreview url={preview.file_url} mediaType={preview.media_type}
          name={preview.file_name} onClose={() => setPreview(null)} />
      )}
    </>
  );
};
