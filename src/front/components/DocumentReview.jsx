import { useEffect, useState } from "react";
import { api } from "../services/api";
import { FilePreview } from "./FilePreview";

// The analyst's side of the document loop: read what the customer sent, then
// accept it or send it back. Whatever the customer uploaded — image, PDF,
// scan, anything — opens here rather than forcing a download first.
const STATE = {
  ACCEPTED: ["LOW", "Accepted"],
  RETURNED: ["HIGH", "Returned to customer"],
  RECEIVED: ["INFO", "Awaiting review"],
  EXPECTED: ["MEDIUM", "Not received"],
};

const stateOf = (doc) => {
  if (!doc.has_file) return "EXPECTED";
  if (doc.rejection_reason) return "RETURNED";
  return doc.status === "VERIFIED" ? "ACCEPTED" : "RECEIVED";
};

const fileSize = (bytes) => {
  if (!bytes) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export const DocumentReview = ({ customerId, documents, canReview, onChange }) => {
  const [preview, setPreview] = useState(null);
  const [returning, setReturning] = useState(null);   // the document being sent back
  const [reasons, setReasons] = useState([]);
  const [reasonCode, setReasonCode] = useState("");
  const [freeText, setFreeText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (canReview) api.rejectionReasons().then(setReasons).catch(() => {});
  }, [canReview]);

  const act = async (doc, payload) => {
    setBusy(true); setError(null);
    try {
      await api.reviewDocument(customerId, doc.id, payload);
      setReturning(null); setReasonCode(""); setFreeText("");
      onChange();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const withFile = (documents || []).filter((d) => d.has_file);

  return (
    <div className="co-card">
      <div className="section-title">Documents ({withFile.length})</div>
      {error && <div className="alert alert-danger py-2">{error}</div>}
      {withFile.length === 0 && (
        <div className="empty">Nothing received yet.</div>
      )}

      <div className="co-rows">
      {withFile.map((d) => {
        const [chip, label] = STATE[stateOf(d)];
        return (
          <div key={d.id}>
            <div className="work-row">
              <span className={`dotsev ${chip}`} />
              <div className="grow">
                <div className="title">
                  <button type="button" className="kf-doc-link"
                    onClick={() => setPreview(d)}>
                    {d.file_name || d.doc_type}
                  </button>
                </div>
                <div className="meta">
                  {d.doc_type}
                  {fileSize(d.file_size) ? ` · ${fileSize(d.file_size)}` : ""}
                  {d.uploaded_at ? ` · ${new Date(d.uploaded_at).toLocaleDateString()}` : ""}
                </div>
                {/* What the customer said they were sending. */}
                {d.description && (
                  <div className="meta"><i className="fa-solid fa-quote-left" /> {d.description}</div>
                )}
                {d.rejection_reason && (
                  <div className="meta" style={{ color: "var(--sev-high)" }}>
                    Returned: {d.rejection_reason}
                  </div>
                )}
              </div>
              <span className={`chip ${chip}`}>{label}</span>
              {canReview && (
                <>
                  <button className="btn btn-sm btn-outline-secondary"
                    onClick={() => setPreview(d)}>Open</button>
                  {stateOf(d) !== "ACCEPTED" && (
                    <button className="btn btn-sm btn-co" disabled={busy}
                      onClick={() => act(d, { decision: "ACCEPT" })}>Accept</button>
                  )}
                  <button className="btn btn-sm btn-outline-danger" disabled={busy}
                    onClick={() => { setReturning(d); setReasonCode(""); setFreeText(""); }}>
                    Return
                  </button>
                </>
              )}
            </div>

            {returning?.id === d.id && (
              <div className="dr-return">
                <div className="meta" style={{ marginBottom: ".4rem" }}>
                  The customer sees this wording. Describe the document — not
                  what your review found.
                </div>
                <select className="form-select form-select-sm" value={reasonCode}
                  onChange={(e) => setReasonCode(e.target.value)}>
                  <option value="">Choose a reason…</option>
                  {reasons.map((r) => (
                    <option key={r.code} value={r.code}>{r.message}</option>
                  ))}
                </select>
                <input className="form-control form-control-sm"
                  style={{ marginTop: ".4rem" }}
                  placeholder="…or write your own"
                  value={freeText} onChange={(e) => setFreeText(e.target.value)} />
                <div className="d-flex gap-2" style={{ marginTop: ".5rem" }}>
                  <button className="btn btn-sm btn-outline-secondary"
                    onClick={() => setReturning(null)}>Cancel</button>
                  <button className="btn btn-sm btn-danger"
                    disabled={busy || (!reasonCode && freeText.trim().length < 5)}
                    onClick={() => act(d, {
                      decision: "RETURN",
                      reason_code: freeText.trim() ? "" : reasonCode,
                      reason: freeText.trim(),
                    })}>
                    Send it back
                  </button>
                </div>
              </div>
            )}
          </div>
        );
      })}
      </div>

      {preview && (
        <FilePreview url={preview.file_url} mediaType={preview.media_type}
          name={preview.file_name} onClose={() => setPreview(null)} />
      )}
    </div>
  );
};
