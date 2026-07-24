import { useEffect, useState, useCallback } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../services/api";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { can } from "../permissions/can";
import { AlertDetails } from "../components/AlertDetails";
import { DeleteCustomerModal } from "../components/DeleteCustomerModal";
import { RowMenu } from "../components/RowMenu";
import { DocumentReview } from "../components/DocumentReview";
import { PortalAccess } from "../components/PortalAccess";
import { MatchDetails } from "../components/MatchDetails";

const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : "—");

// Human labels for the transaction-monitoring detector codes.
const DETECTOR_LABELS = {
  LARGE_AMOUNT: "Large amount",
  HIGH_RISK_COUNTRY: "High-risk country",
  STRUCTURING: "Structuring",
  VELOCITY: "Velocity",
  RAPID_PASSTHROUGH: "Pass-through",
  CASH_INTENSIVE: "Cash-intensive",
};
const money = (a, c) => `${Number(a || 0).toLocaleString()} ${c || ""}`.trim();

// Map a screening-match status to a severity chip colour.
const MATCH_SEV = {
  CONFIRMED: "CRITICAL", ESCALATED: "HIGH",
  POTENTIAL: "MEDIUM", UNDER_REVIEW: "MEDIUM", FALSE_POSITIVE: "LOW",
};

// Render "who owns X" as a nested tree from the ownership edges.
// onRemove (optional): deactivates an erroneous edge — a FALSE_POSITIVE case
// decision never rewrites KYB data, this is how staff corrects the graph.
const OwnershipTree = ({ nodeId, nodes, edges, factor = 1, depth = 0, onRemove }) => {
  const node = nodes.find((n) => n.id === nodeId);
  if (!node) return null;
  const owners = edges.filter((e) => e.owned_party_id === nodeId);
  return (
    <div style={{ marginLeft: depth ? 16 : 0, paddingLeft: depth ? 10 : 0, borderLeft: depth ? "2px solid var(--co-border)" : "none" }}>
      <div style={{ padding: ".2rem 0" }}>
        <i className={`fa-solid ${node.kind === "PERSON" ? "fa-user" : "fa-building"}`} style={{ color: "var(--co-muted)", marginRight: 6 }} />
        <b>{node.name}</b>
        {node.kind === "ORGANIZATION" && node.country_of_incorporation ? (
          <span className="muted" style={{ fontSize: ".8rem" }}> · {node.country_of_incorporation}</span>
        ) : null}
      </div>
      {owners.map((e) => (
        <div key={e.id}>
          <div style={{ marginLeft: 16, fontSize: ".82rem", color: "var(--co-muted)" }}>
            ▲ owns {e.percentage}% {e.relationship_type !== "SHAREHOLDER" ? `(${e.relationship_type})` : ""}
            {onRemove && (
              <button type="button" className="kf-doc-remove"
                title="Remove this link (audited — the graph and UBOs recompute)"
                onClick={() => onRemove(e)}>×</button>
            )}
          </div>
          <OwnershipTree nodeId={e.owner_party_id} nodes={nodes} edges={edges} depth={depth + 1} onRemove={onRemove} />
        </div>
      ))}
    </div>
  );
};

export const Customer360 = () => {
  const { id } = useParams();
  const { store } = useGlobalReducer();
  const [data, setData] = useState(null);
  const [graph, setGraph] = useState(null);
  const [addresses, setAddresses] = useState([]);
  const [fields, setFields] = useState([]);
  const [txns, setTxns] = useState([]);
  const [txForm, setTxForm] = useState({ direction: "INBOUND", amount: "", currency: "EUR", method: "SEPA", counterparty_name: "", counterparty_country: "" });
  const [txBusy, setTxBusy] = useState(false);
  const [error, setError] = useState(null);
  const [screening, setScreening] = useState(false);
  const [ownerForm, setOwnerForm] = useState({ owner_name: "", owner_kind: "PERSON", relationship_type: "SHAREHOLDER", percentage: "", country: "" });
  const [addrForm, setAddrForm] = useState({ number: "", street: "", city: "", postal_code: "", country: "" });
  const [fieldForm, setFieldForm] = useState({ field_key: "", value: "", source: "manual" });
  const [kyb, setKyb] = useState(null);
  const [kybBusy, setKybBusy] = useState(false);
  const [openAlert, setOpenAlert] = useState(null);
  const [openTask, setOpenTask] = useState(null);          // task whose details are unfolded
  const [openReview, setOpenReview] = useState(null);      // review whose details are unfolded
  const [completing, setCompleting] = useState(null);      // review being completed
  const [reviewForm, setReviewForm] = useState({ decision: "APPROVED", reason: "" });
  const [enriching, setEnriching] = useState(false);
  const [enrichNote, setEnrichNote] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [openingChat, setOpeningChat] = useState(false);
  const [showPortalAccess, setShowPortalAccess] = useState(false);

  // The client conversation belongs to the customer file: opening it joins the
  // team room rather than starting a private thread with whoever clicked.
  const openClientChat = async () => {
    setOpeningChat(true);
    try {
      const room = await api.openCustomerRoom(id);
      navigate(`/chat?room=${room.id}`);
    } catch (e) { setError(e.message); }
    finally { setOpeningChat(false); }
  };
  const navigate = useNavigate();

  const runEnrichment = async () => {
    setEnriching(true); setError(null); setEnrichNote(null);
    try {
      const report = await api.enrich(id);
      setEnrichNote(report.summary);
      await load(); loadKyb();
    } catch (e) { setError(e.message); }
    finally { setEnriching(false); }
  };

  const runKybLookup = async () => {
    setKybBusy(true); setError(null);
    try { setKyb(await api.kybLookup(id)); }
    catch (e) { setError(e.message); }
    finally { setKybBusy(false); }
  };

  const load = useCallback(() => api.customer(id).then(setData).catch((e) => setError(e.message)), [id]);
  const loadKyb = useCallback(() => {
    api.ownership(id).then(setGraph).catch(() => setGraph(null));
    api.addresses(id).then(setAddresses).catch(() => setAddresses([]));
    api.fields(id).then(setFields).catch(() => setFields([]));
    api.transactions(id).then(setTxns).catch(() => setTxns([]));
  }, [id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadKyb(); }, [loadKyb, screening]);

  const submitTx = async (e) => {
    e.preventDefault();
    setError(null); setTxBusy(true);
    try {
      await api.ingestTransaction(id, { ...txForm, amount: Number(txForm.amount) || 0 });
      setTxForm({ direction: "INBOUND", amount: "", currency: "EUR", method: "SEPA", counterparty_name: "", counterparty_country: "" });
      await load(); loadKyb();   // reload: a flag may have raised an alert
    } catch (err) { setError(err.message); }
    finally { setTxBusy(false); }
  };

  const submitField = async (e) => {
    e.preventDefault();
    setError(null);
    try {
      await api.setField(id, fieldForm);
      setFieldForm({ field_key: "", value: "", source: "manual" });
      await load(); loadKyb();
    } catch (err) { setError(err.message); }
  };

  const verifyField = async (fid) => {
    try { await api.verifyField(id, fid); await load(); loadKyb(); }
    catch (err) { setError(err.message); }
  };
  const removeField = async (fid) => {
    try { await api.removeField(id, fid); await load(); loadKyb(); }
    catch (err) { setError(err.message); }
  };

  const requestInfo = async () => {
    setError(null);
    try { await api.requestInfo(id); await load(); }
    catch (err) { setError(err.message); }
  };

  const startReview = async (rid) => {
    try { await api.startReview(rid); await load(); } catch (err) { setError(err.message); }
  };
  const completeTask = async (tid) => {
    try { await api.completeTask(tid); await load(); } catch (err) { setError(err.message); }
  };
  // Completing a review is a decision, not a click: an inline panel asks for
  // approve/reject + a reason (same discipline as workflow step findings).
  const completeReview = async (rid) => {
    try {
      await api.completeReview(rid, {
        decision: reviewForm.decision, reason: reviewForm.reason.trim() });
      setCompleting(null); setReviewForm({ decision: "APPROVED", reason: "" });
      await load();
    } catch (err) { setError(err.message); }
  };

  const removeOwner = async (edge) => {
    setError(null);
    try { await api.removeOwnership(id, edge.id); await load(); loadKyb(); }
    catch (err) { setError(err.message); }
  };

  const submitOwner = async (e) => {
    e.preventDefault();
    setError(null);
    try {
      await api.addOwnership(id, { ...ownerForm, percentage: Number(ownerForm.percentage) || 0 });
      setOwnerForm({ owner_name: "", owner_kind: "PERSON", relationship_type: "SHAREHOLDER", percentage: "", country: "" });
      await load(); loadKyb();
    } catch (err) { setError(err.message); }
  };

  const submitAddress = async (e) => {
    e.preventDefault();
    setError(null);
    try {
      // Same shape as the KYC form's address block; line1 = number + street.
      await api.addAddress(id, {
        line1: `${addrForm.number} ${addrForm.street}`.trim(),
        city: addrForm.city, postal_code: addrForm.postal_code,
        country: addrForm.country,
      });
      setAddrForm({ number: "", street: "", city: "", postal_code: "", country: "" });
      await load(); loadKyb();
    } catch (err) { setError(err.message); }
  };

  const runScreening = async () => {
    setScreening(true);
    try {
      await api.screen(id);
      // The chain may run asynchronously via Celery — poll a few times.
      let tries = 0;
      const poll = setInterval(async () => {
        tries += 1;
        await load();
        if (tries >= 4) { clearInterval(poll); setScreening(false); }
      }, 1200);
    } catch (e) { setError(e.message); setScreening(false); }
  };

  if (error) return <div className="alert alert-danger">{error}</div>;
  if (!data) return <div className="empty">Loading customer…</div>;

  const { customer, risk, open_cases, tasks, documents, recent_events,
          changes_since_review, screening_matches = [], ubos = [],
          completeness, reviews = [], open_alerts = [] } = data;
  const REQ_SEV = { VERIFIED: "LOW", RECEIVED: "MEDIUM", MISSING: "CRITICAL", WAIVED: "INFO" };
  const REVIEW_SEV = { OVERDUE: "CRITICAL", DUE: "HIGH", IN_PROGRESS: "MEDIUM", SCHEDULED: "INFO", COMPLETED: "LOW" };

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start",
                    flexWrap: "wrap", gap: ".6rem .9rem", marginBottom: "1rem" }}>
        <div>
          <div className="muted" style={{ fontSize: ".8rem" }}><Link to="/customers">← Customers</Link></div>
          <h3 style={{ margin: ".2rem 0" }}>{customer.name}</h3>
          <div className="muted">
            {customer.customer_type} · {customer.country || "—"}
            {customer.business_activity ? ` · ${customer.business_activity}` : ""}
          </div>
        </div>
        <div className="d-flex gap-2 flex-wrap">
          {can(store.user, "kyc.view") && (
            <Link to={`/customers/${id}/kyc-form`} className="btn btn-outline-secondary">
              <i className="fa-solid fa-clipboard-check" /> KYC form
            </Link>
          )}
          <Link to={`/assistant?customer=${id}`} className="btn btn-outline-secondary">
            <i className="fa-solid fa-robot" /> Ask Copilot
          </Link>
          <button className="btn btn-outline-secondary" title="Invite this client to their portal"
            onClick={() => setShowPortalAccess(true)}>
            <i className="fa-solid fa-user-shield" /> Portal access
          </button>
          <button className="btn btn-outline-secondary" onClick={openClientChat}
            disabled={openingChat}
            title="The conversation with this client, read by the team on the file">
            <i className="fa-solid fa-comments" />{" "}
            {openingChat ? "Opening…" : "Client chat"}
          </button>
          {can(store.user, "kyc.edit") && (
            <button className="btn btn-outline-secondary" onClick={runEnrichment}
              disabled={enriching} title="Auto-fill from public sources (registries, LEI, adverse media)">
              <i className="fa-solid fa-wand-magic-sparkles" /> {enriching ? "Enriching…" : "Enrich"}
            </button>
          )}
          {can(store.user, "data.export") && (
            <a className="btn btn-outline-secondary" href={api.dataExportUrl(id)}
              title="Export everything held on this subject (GDPR right of access)">
              <i className="fa-solid fa-file-export" /> Export data
            </a>
          )}
          <button className="btn btn-co" onClick={runScreening} disabled={screening}>
            <i className="fa-solid fa-magnifying-glass" /> {screening ? "Screening…" : "Run screening"}
          </button>
          <RowMenu items={[
            can(store.user, "customer.update") && {
              label: "Remove customer…", icon: "fa-solid fa-trash", danger: true,
              onClick: () => setConfirmDelete(true),
            },
          ]} />
        </div>
      </div>

      {showPortalAccess && (
        <PortalAccess customerId={id} onClose={() => setShowPortalAccess(false)} />
      )}

      {confirmDelete && (
        <DeleteCustomerModal
          customer={customer}
          onClose={() => setConfirmDelete(false)}
          onDeleted={() => navigate("/customers")}
          onArchived={() => { setConfirmDelete(false); load(); }}
        />
      )}

      {enrichNote && (
        <div className="alert alert-success py-2">
          <i className="fa-solid fa-wand-magic-sparkles" /> {enrichNote}
        </div>
      )}

      <div className="row g-3">
        {/* Risk — explainable */}
        <div className="col-md-5">
          <div className="co-card">
            <div className="section-title">Risk assessment</div>
            <div style={{ display: "flex", alignItems: "baseline", gap: ".6rem" }}>
              <span className="risk-badge">{customer.risk_score}<small> / 100</small></span>
              <span className={`chip ${customer.risk_level}`}>{customer.risk_level}</span>
              {risk && risk.methodology_version && (
                <span className="muted" style={{ fontSize: ".72rem" }}>methodology {risk.methodology_version}</span>
              )}
            </div>

            {risk && risk.factors && risk.factors.length > 0 ? (
              <div style={{ marginTop: ".75rem" }}>
                <div className="muted" style={{ fontSize: ".8rem", marginBottom: ".25rem" }}>Why?</div>
                {risk.factors.map((f) => (
                  <div className="factor" key={f.code}>
                    <span>{f.label}</span>
                    <span className="impact">+{f.impact}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted mt-2" style={{ fontSize: ".85rem" }}>No risk drivers yet. Run screening.</p>
            )}

            {risk && risk.required_actions && risk.required_actions.length > 0 && (
              <div style={{ marginTop: ".75rem" }}>
                <div className="muted" style={{ fontSize: ".8rem", marginBottom: ".25rem" }}>Required actions</div>
                <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                  {risk.required_actions.map((a, i) => <li key={i} style={{ fontSize: ".88rem" }}>{a}</li>)}
                </ul>
              </div>
            )}
          </div>
        </div>

        <div className="col-md-7">
          {/* Changes since review */}
          <div className="co-card">
            <div className="section-title">Changes since last review {changes_since_review.length > 0 && `(${changes_since_review.length})`}</div>
            {changes_since_review.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No changes detected.</div>}
            <div className="co-rows">
            {changes_since_review.map((e) => (
              <div className="work-row" key={e.id}>
                <span className={`dotsev ${e.severity}`} />
                <div className="grow">
                  <div className="title">{e.event_type.replace(/_/g, " ")}</div>
                  <div className="meta">{e.source} · {fmt(e.detected_at)}</div>
                </div>
                <span className={`chip ${e.severity}`}>{e.severity}</span>
              </div>
            ))}
            </div>
          </div>

          {/* Open cases */}
          <div className="co-card">
            <div className="section-title">Open cases {open_cases.length > 0 && `(${open_cases.length})`}</div>
            {open_cases.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No open cases.</div>}
            <div className="co-rows">
            {open_cases.map((c) => (
              <div className="work-row" key={c.id}>
                <span className={`dotsev ${c.priority}`} />
                <div className="grow">
                  <div className="title"><Link to={`/cases/${c.id}`}>{c.title}</Link></div>
                  <div className="meta">{c.case_type} · {c.status}</div>
                </div>
                <Link to={`/cases/${c.id}`} className="btn btn-sm btn-outline-secondary">Investigate</Link>
              </div>
            ))}
            </div>
          </div>
        </div>
      </div>

      {/* What the customer actually sent, and the decision on each piece. */}
      <DocumentReview customerId={id} documents={documents}
        canReview={can(store.user, "document.verify")} onChange={load} />

      {/* Compliance completeness — what's missing before the review */}
      {completeness && (
        <div className="co-card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: ".4rem" }}>
            <div className="section-title" style={{ marginBottom: 0 }}>
              Compliance completeness — {completeness.completeness_pct}%
              <span className="muted" style={{ fontWeight: 400 }}> ({completeness.satisfied}/{completeness.total})</span>
            </div>
            {completeness.missing_count > 0 && can(store.user, "kyc.review") && (
              <button className="btn btn-sm btn-co" onClick={requestInfo}>
                <i className="fa-solid fa-paper-plane" /> Request missing info
              </button>
            )}
          </div>
          <div style={{ background: "var(--co-border)", borderRadius: 6, height: 10, marginBottom: ".6rem" }}>
            <div style={{ width: `${completeness.completeness_pct}%`, height: 10, borderRadius: 6,
              background: completeness.completeness_pct >= 80 ? "var(--sev-low)" : completeness.completeness_pct >= 40 ? "var(--sev-medium)" : "var(--sev-high)" }} />
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: ".35rem" }}>
            {completeness.requirements.map((r) => (
              <span key={r.code} className={`chip ${REQ_SEV[r.status] || "INFO"}`} title={r.kind}>
                {r.status === "VERIFIED" ? "✓" : r.status === "MISSING" ? "✗" : "•"} {r.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Reviews & open alerts */}
      <div className="row g-3 mt-0">
        <div className="col-md-6">
          <div className="co-card">
            <div className="section-title">Reviews {reviews.length > 0 && `(${reviews.length})`}</div>
            {reviews.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No reviews.</div>}
            <div className="co-rows">
            {reviews.map((r) => (
              <div key={r.id} style={{ borderBottom: "1px solid var(--co-border)" }}>
                <div className="work-row" style={{ borderBottom: "none" }}>
                  <span className={`dotsev ${REVIEW_SEV[r.status] || "INFO"}`} />
                  <div className="grow">
                    <div className="title">{r.review_type.replace(/_/g, " ")}</div>
                    <div className="meta">{r.trigger}{r.due_at ? ` · due ${new Date(r.due_at).toLocaleDateString()}` : ""}</div>
                  </div>
                  <span className={`chip ${REVIEW_SEV[r.status] || "INFO"}`}>{r.status}</span>
                  <button className="btn btn-sm btn-outline-secondary"
                    onClick={() => setOpenReview(openReview === r.id ? null : r.id)}>
                    {openReview === r.id ? "Hide" : "Details"}
                  </button>
                  {can(store.user, "kyc.review") && r.status === "DUE" && (
                    <button className="btn btn-sm btn-outline-secondary" onClick={() => startReview(r.id)}>Start</button>
                  )}
                  {can(store.user, "kyc.review") && r.status === "IN_PROGRESS" && (
                    <button className="btn btn-sm btn-outline-success"
                      onClick={() => { setCompleting(completing === r.id ? null : r.id);
                                       setReviewForm({ decision: "APPROVED", reason: "" }); }}>
                      Complete
                    </button>
                  )}
                </div>
                {openReview === r.id && (
                  <div className="md-panel" style={{ marginLeft: "1.4rem" }}>
                    {[["Status", r.status],
                      ["Trigger", r.trigger],
                      ["Scheduled", r.scheduled_for && new Date(r.scheduled_for).toLocaleDateString()],
                      ["Started", r.started_at && new Date(r.started_at).toLocaleString()],
                      ["Completed", r.completed_at && new Date(r.completed_at).toLocaleString()],
                      ["Decision", r.decision],
                      ["Reason", r.decision_reason],
                      ["Methodology", r.methodology_version && `v${r.methodology_version}`],
                    ].map(([label, value]) => value ? (
                      <div className="md-row" key={label}>
                        <span className="md-label">{label}</span>
                        <span className="md-value">{value}</span>
                      </div>
                    ) : null)}
                  </div>
                )}
                {completing === r.id && (
                  <div className="wf-complete" style={{ marginLeft: "1.4rem", marginBottom: ".6rem" }}>
                    <div className="meta" style={{ marginBottom: ".35rem" }}>
                      The decision and its reason are recorded on the review (audited).
                    </div>
                    <select className="form-select form-select-sm" value={reviewForm.decision}
                      onChange={(e) => setReviewForm({ ...reviewForm, decision: e.target.value })}>
                      <option value="APPROVED">Approve — relationship continues</option>
                      <option value="REJECTED">Reject — escalate / exit</option>
                    </select>
                    <input className="form-control form-control-sm" style={{ marginTop: ".4rem" }}
                      placeholder="What did the review find? (min 5 chars)"
                      value={reviewForm.reason}
                      onChange={(e) => setReviewForm({ ...reviewForm, reason: e.target.value })} />
                    <div className="d-flex gap-2" style={{ marginTop: ".5rem" }}>
                      <button className="btn btn-sm btn-outline-secondary"
                        onClick={() => setCompleting(null)}>Cancel</button>
                      <button className="btn btn-sm btn-co"
                        disabled={reviewForm.reason.trim().length < 5}
                        onClick={() => completeReview(r.id)}>Record decision</button>
                    </div>
                  </div>
                )}
              </div>
            ))}
            </div>
          </div>
        </div>
        <div className="col-md-6">
          <div className="co-card">
            <div className="section-title">Open compliance alerts {open_alerts.length > 0 && `(${open_alerts.length})`}</div>
            {open_alerts.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No open alerts.</div>}
            <div className="co-rows">
            {open_alerts.map((a) => (
              <div key={a.id} style={{ borderBottom: "1px solid var(--co-border)" }}>
                <div className="work-row" style={{ borderBottom: "none" }}>
                  <span className={`dotsev ${a.severity}`} />
                  <div className="grow"><div className="title">{a.title}</div><div className="meta">{a.status} · {a.source}</div></div>
                  <span className={`chip ${a.severity}`}>{a.severity}</span>
                  <button className="btn btn-sm btn-outline-secondary"
                    onClick={() => setOpenAlert(openAlert === a.id ? null : a.id)}>
                    {openAlert === a.id ? "Hide" : "Details"}
                  </button>
                </div>
                {openAlert === a.id && <AlertDetails details={a.details} />}
              </div>
            ))}
            </div>
          </div>
        </div>
      </div>

      <div className="row g-3 mt-0">
        <div className="col-md-6">
          <div className="co-card">
            <div className="section-title">Open tasks {tasks.length > 0 && `(${tasks.length})`}</div>
            {tasks.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>None.</div>}
            <div className="co-rows">
            {tasks.map((t) => {
              const overdue = t.due_at && new Date(t.due_at) < new Date();
              return (
              <div key={t.id} style={{ borderBottom: "1px solid var(--co-border)" }}>
                <div className="work-row" style={{ borderBottom: "none" }}>
                  <span className={`dotsev ${t.priority}`} />
                  <div className="grow">
                    <div className="title">{t.title}</div>
                    <div className="meta">
                      {t.task_type}
                      {t.due_at && <> · due <span style={overdue ? { color: "var(--sev-high)", fontWeight: 600 } : {}}>
                        {new Date(t.due_at).toLocaleDateString()}{overdue ? " (overdue)" : ""}
                      </span></>}
                    </div>
                  </div>
                  <span className={`chip ${t.priority}`}>{t.priority}</span>
                  <button className="btn btn-sm btn-outline-secondary"
                    onClick={() => setOpenTask(openTask === t.id ? null : t.id)}>
                    {openTask === t.id ? "Hide" : "Details"}
                  </button>
                  {can(store.user, "task.complete") && (
                    <button className="btn btn-sm btn-outline-success"
                      title="Mark this task as done (audited)"
                      onClick={() => completeTask(t.id)}>
                      <i className="fa-solid fa-check" /> Done
                    </button>
                  )}
                </div>
                {openTask === t.id && (
                  <div className="md-panel" style={{ marginLeft: "1.4rem" }}>
                    {[["Type", t.task_type],
                      ["Status", t.status],
                      ["Due", t.due_at && `${new Date(t.due_at).toLocaleDateString()}${overdue ? " · overdue" : ""}`],
                      ["Created", t.created_at && fmt(t.created_at)],
                      ["Assigned to", t.assigned_to_name || "Unassigned"],
                      ["Requirement", t.requirement_code],
                    ].map(([label, value]) => value ? (
                      <div className="md-row" key={label}>
                        <span className="md-label">{label}</span>
                        <span className="md-value">{value}</span>
                      </div>
                    ) : null)}
                    {t.case_id && (
                      <div className="md-row">
                        <span className="md-label">Case</span>
                        <span className="md-value">
                          <Link to={`/cases/${t.case_id}`}>
                            {t.case_title || `Case #${t.case_id}`}
                          </Link>
                        </span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );})}
            </div>
          </div>
        </div>
        <div className="col-md-6">
          <div className="co-card">
            <div className="section-title">Recent events {recent_events.length > 0 && `(${recent_events.length})`}</div>
            {recent_events.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No events yet.</div>}
            <div className="co-rows">
            {recent_events.map((e) => (
              <div className="tl-item" key={e.id}>
                <span className="when">{fmt(e.detected_at)}</span>
                <span className={`dotsev ${e.severity}`} style={{ marginTop: 4 }} />
                <span style={{ fontSize: ".88rem" }}>{e.event_type.replace(/_/g, " ")}</span>
              </div>
            ))}
            </div>
          </div>
        </div>
      </div>

      <div className="row g-3 mt-0">
        {/* Screening matches — first-class records with their own lifecycle */}
        <div className="col-md-6">
          <div className="co-card">
            <div className="section-title">Screening matches {screening_matches.length > 0 && `(${screening_matches.length})`}</div>
            {screening_matches.length === 0 && (
              <div className="muted" style={{ fontSize: ".88rem" }}>No matches. Run screening.</div>
            )}
            <div className="co-rows">
            {screening_matches.map((m) => (
              <div className="work-row" key={m.id}>
                <span className={`dotsev ${MATCH_SEV[m.status] || "INFO"}`} />
                <div className="grow">
                  <div className="title">{m.match_type.replace(/_/g, " ")} · {m.matched_name}</div>
                  <div className="meta">
                    {m.source} · score {m.match_score}%
                    {m.decision_reason ? ` · ${m.decision_reason}` : ""}
                  </div>
                  <MatchDetails match={m} />
                </div>
                <span className={`chip ${MATCH_SEV[m.status] || "INFO"}`}>{m.status.replace(/_/g, " ")}</span>
              </div>
            ))}
            </div>
          </div>
        </div>

        {/* Ownership & UBOs */}
        <div className="col-md-6">
          {customer.customer_type === "COMPANY" && can(store.user, "kyb.view") && (
            <div className="co-card" style={{ marginBottom: "1rem" }}>
              <div className="d-flex justify-content-between align-items-start">
                <div className="section-title">Company registry (Companies House)</div>
                <button className="btn btn-sm btn-outline-secondary"
                  onClick={runKybLookup} disabled={kybBusy}>
                  <i className="fa-solid fa-building-columns" /> {kybBusy ? "Looking up…" : "Lookup"}
                </button>
              </div>
              {!kyb && (
                <div className="muted" style={{ fontSize: ".85rem" }}>
                  Live lookup against the UK register (needs a Companies House API key).
                </div>
              )}
              {kyb && (
                <div style={{ fontSize: ".88rem" }}>
                  <div><b>{kyb.data?.company_name}</b> · #{kyb.data?.company_number}
                    {" "}<span className={`chip ${kyb.status === "PASSED" ? "LOW" : "HIGH"}`}>{kyb.data?.company_status || kyb.status}</span></div>
                  <div className="muted">
                    {kyb.data?.company_type} · incorporated {kyb.data?.incorporated_on || "—"}
                    {kyb.data?.sic_codes?.length ? ` · SIC ${kyb.data.sic_codes.join(", ")}` : ""}
                  </div>
                  {kyb.data?.registered_office && <div className="muted">{kyb.data.registered_office}</div>}
                  {(kyb.data?.officers || []).slice(0, 5).map((o, i) => (
                    <div className="meta" key={i}>
                      <i className="fa-solid fa-user-tie muted" /> {o.name} — {o.role}
                      {o.resigned_on ? " (resigned)" : ""}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="co-card">
            <div className="section-title">Ownership &amp; UBOs</div>
            {ubos.length === 0 && (
              <div className="muted" style={{ fontSize: ".88rem" }}>
                No ownership structure recorded.
              </div>
            )}
            {ubos.length > 0 && (
              <div style={{ marginBottom: ".6rem" }}>
                {ubos.map((u) => (
                  <div className="work-row" key={u.party.id}>
                    <span className={`dotsev ${u.is_ubo ? "HIGH" : "INFO"}`} />
                    <div className="grow">
                      <div className="title">{u.party.name}</div>
                      <div className="meta">
                        {u.party.nationality || "—"}
                        {u.roles && u.roles.length > 0
                          ? ` · ${u.roles.map((r) => r.toLowerCase()).join(", ")}`
                          : u.via_control ? " · control" : ""}
                      </div>
                    </div>
                    <span className={`chip ${u.is_ubo ? "HIGH" : "INFO"}`}>
                      {u.effective_ownership}%{u.is_ubo ? " · UBO" : ""}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {graph && graph.directors && graph.directors.length > 0 && (
              <div style={{ marginTop: ".5rem" }}>
                <div className="muted" style={{ fontSize: ".78rem", marginBottom: ".2rem" }}>Directors</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: ".35rem" }}>
                  {graph.directors.map((d) => (
                    <span key={d.id} className="chip INFO">
                      <i className="fa-solid fa-user-tie" /> {d.name}
                      {can(store.user, "kyb.edit") && d.edge_id && (
                        <button type="button" className="kf-doc-remove"
                          title="Remove this director (audited)"
                          onClick={() => removeOwner({ id: d.edge_id })}>×</button>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {graph && graph.graph && graph.graph.root_id && (
              <div style={{ fontSize: ".9rem", marginTop: ".4rem" }}>
                <div className="muted" style={{ fontSize: ".78rem", marginBottom: ".2rem" }}>Structure</div>
                <OwnershipTree nodeId={graph.graph.root_id} nodes={graph.graph.nodes} edges={graph.graph.edges}
                  onRemove={can(store.user, "kyb.edit") ? removeOwner : null} />
              </div>
            )}
            {can(store.user, "kyb.edit") && (
              <form onSubmit={submitOwner} className="row g-1 align-items-end" style={{ marginTop: ".75rem", borderTop: "1px solid var(--co-border)", paddingTop: ".6rem" }}>
                <div className="col-12 col-md-3">
                  <input className="form-control form-control-sm" placeholder="Name" required
                    value={ownerForm.owner_name} onChange={(e) => setOwnerForm({ ...ownerForm, owner_name: e.target.value })} />
                </div>
                <div className="col-6 col-md-3">
                  <select className="form-select form-select-sm" value={ownerForm.relationship_type}
                    onChange={(e) => setOwnerForm({ ...ownerForm, relationship_type: e.target.value })}>
                    <option value="SHAREHOLDER">Shareholder</option>
                    <option value="DIRECTOR">Director</option>
                    <option value="UBO">UBO</option>
                    <option value="CONTROL">Control</option>
                    <option value="SETTLOR">Settlor</option>
                    <option value="TRUSTEE">Trustee</option>
                    <option value="PROTECTOR">Protector</option>
                    <option value="BENEFICIARY">Beneficiary</option>
                  </select>
                </div>
                <div className="col-6 col-md-2">
                  <select className="form-select form-select-sm" value={ownerForm.owner_kind}
                    onChange={(e) => setOwnerForm({ ...ownerForm, owner_kind: e.target.value })}>
                    <option value="PERSON">Person</option>
                    <option value="ORGANIZATION">Company</option>
                    <option value="TRUST">Trust</option>
                  </select>
                </div>
                <div className="col-6 col-md-2">
                  <input className="form-control form-control-sm" placeholder="%" type="number" min="0" max="100"
                    value={ownerForm.percentage} onChange={(e) => setOwnerForm({ ...ownerForm, percentage: e.target.value })} />
                </div>
                <div className="col-6 col-md-2">
                  <button className="btn btn-sm btn-co w-100">Add</button>
                </div>
              </form>
            )}
          </div>
        </div>
      </div>

      {/* Addresses — with history */}
      <div className="row g-3 mt-0">
        <div className="col-md-6">
          <div className="co-card">
            <div className="section-title">Addresses</div>
            {addresses.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No address on file.</div>}
            {addresses.map((a) => (
              <div className="work-row" key={a.id}>
                <span className={`dotsev ${a.is_current ? "LOW" : "INFO"}`} />
                <div className="grow">
                  <div className="title" style={a.is_current ? {} : { textDecoration: "line-through", opacity: 0.6 }}>
                    {a.line1}
                    {(a.postal_code || a.city) ? `, ${[a.postal_code, a.city].filter(Boolean).join(" ")}` : ""}
                    {a.country ? `, ${a.country}` : ""}
                  </div>
                  <div className="meta">
                    {a.address_type} · {a.is_current ? "current" : `until ${fmt(a.valid_to)}`}
                  </div>
                </div>
                {a.is_current && <span className="chip LOW">CURRENT</span>}
              </div>
            ))}
            {can(store.user, "kyc.edit") && (
              <form onSubmit={submitAddress} className="row g-1 align-items-end" style={{ marginTop: ".6rem", borderTop: "1px solid var(--co-border)", paddingTop: ".6rem" }}>
                <div className="col-3 col-md-2">
                  <input className="form-control form-control-sm" placeholder="N°"
                    value={addrForm.number} onChange={(e) => setAddrForm({ ...addrForm, number: e.target.value })} />
                </div>
                <div className="col-9 col-md-10">
                  <input className="form-control form-control-sm" placeholder="Street name" required
                    value={addrForm.street} onChange={(e) => setAddrForm({ ...addrForm, street: e.target.value })} />
                </div>
                <div className="col-6 col-md-4">
                  <input className="form-control form-control-sm" placeholder="City"
                    value={addrForm.city} onChange={(e) => setAddrForm({ ...addrForm, city: e.target.value })} />
                </div>
                <div className="col-6 col-md-3">
                  <input className="form-control form-control-sm" placeholder="Postal code"
                    value={addrForm.postal_code} onChange={(e) => setAddrForm({ ...addrForm, postal_code: e.target.value })} />
                </div>
                <div className="col-8 col-md-3">
                  <input className="form-control form-control-sm" placeholder="Country"
                    value={addrForm.country} onChange={(e) => setAddrForm({ ...addrForm, country: e.target.value })} />
                </div>
                <div className="col-4 col-md-2">
                  <button className="btn btn-sm btn-co w-100">Add</button>
                </div>
              </form>
            )}
          </div>
        </div>

        {/* KYC data — with provenance */}
        <div className="col-md-6">
          <div className="co-card">
            <div className="section-title">KYC data (provenance)</div>
            {fields.length === 0 && <div className="muted" style={{ fontSize: ".88rem" }}>No fields captured.</div>}
            {fields.map((f) => (
              <div className="work-row" key={f.id}>
                <span className={`dotsev ${f.verified ? "LOW" : "MEDIUM"}`} />
                <div className="grow">
                  <div className="title">{f.field_key}: {f.value || "—"}</div>
                  <div className="meta">
                    source: {f.source}{f.confidence != null ? ` · conf ${Math.round(f.confidence * 100)}%` : ""}
                    {f.verified ? " · verified" : ""}
                  </div>
                </div>
                {f.verified
                  ? <span className="chip LOW">✓ verified</span>
                  : can(store.user, "kyc.approve")
                    ? <button className="btn btn-sm btn-outline-success" onClick={() => verifyField(f.id)}>Verify</button>
                    : <span className="chip MEDIUM">unverified</span>}
                {can(store.user, "kyc.edit") && (
                  <button type="button" className="kf-doc-remove"
                    title="Remove this field (audited — e.g. registry data imported for the wrong company)"
                    onClick={() => removeField(f.id)}>×</button>
                )}
              </div>
            ))}
            {can(store.user, "kyc.edit") && (
              <form onSubmit={submitField} className="row g-1 align-items-end" style={{ marginTop: ".6rem", borderTop: "1px solid var(--co-border)", paddingTop: ".6rem" }}>
                <div className="col-6 col-md-4">
                  <input className="form-control form-control-sm" placeholder="field_key" required
                    value={fieldForm.field_key} onChange={(e) => setFieldForm({ ...fieldForm, field_key: e.target.value })} />
                </div>
                <div className="col-6 col-md-4">
                  <input className="form-control form-control-sm" placeholder="value"
                    value={fieldForm.value} onChange={(e) => setFieldForm({ ...fieldForm, value: e.target.value })} />
                </div>
                <div className="col-6 col-md-2">
                  <input className="form-control form-control-sm" placeholder="source"
                    value={fieldForm.source} onChange={(e) => setFieldForm({ ...fieldForm, source: e.target.value })} />
                </div>
                <div className="col-6 col-md-2">
                  <button className="btn btn-sm btn-co w-100">Set</button>
                </div>
              </form>
            )}
          </div>
        </div>
      </div>

      {/* Transaction monitoring */}
      {can(store.user, "transaction.view") && (
      <div className="row g-3 mt-0">
        <div className="col-12">
          <div className="co-card">
            <div className="section-title">
              Transactions {txns.length > 0 && `(${txns.length}`}
              {txns.filter((t) => t.flagged).length > 0 &&
                <span style={{ color: "var(--sev-high)" }}> · {txns.filter((t) => t.flagged).length} flagged</span>}
              {txns.length > 0 && ")"}
            </div>
            {txns.length === 0 && (
              <div className="muted" style={{ fontSize: ".88rem" }}>
                No transactions recorded. Monitoring runs on each one ingested.
              </div>
            )}
            <div className="co-rows">
            {txns.map((t) => (
              <div className="work-row" key={t.id}>
                <span className={`dotsev ${t.flagged ? "HIGH" : "LOW"}`} />
                <div className="grow">
                  <div className="title">
                    <i className={`fa-solid fa-arrow-${t.direction === "INBOUND" ? "down" : "up"}`}
                      style={{ color: t.direction === "INBOUND" ? "var(--sev-low)" : "var(--sev-high)", marginRight: 6 }} />
                    {money(t.amount, t.currency)}
                    {t.counterparty_name ? ` · ${t.counterparty_name}` : ""}
                    {t.counterparty_country ? ` (${t.counterparty_country})` : ""}
                  </div>
                  <div className="meta">
                    {t.method || "—"} · {t.booked_at ? new Date(t.booked_at).toLocaleDateString() : "—"}
                    {(t.flags || []).length > 0 && (
                      <> · {t.flags.map((f) => (
                        <span key={f} className="chip HIGH" style={{ marginLeft: 4 }}>
                          {DETECTOR_LABELS[f] || f}
                        </span>
                      ))}</>
                    )}
                  </div>
                </div>
                {t.flagged
                  ? <span className="chip HIGH">FLAGGED</span>
                  : <span className="chip LOW">clear</span>}
              </div>
            ))}
            </div>
            {can(store.user, "transaction.ingest") && (
              <form onSubmit={submitTx} className="row g-1 align-items-end"
                style={{ marginTop: ".75rem", borderTop: "1px solid var(--co-border)", paddingTop: ".6rem" }}>
                <div className="col-6 col-md-2">
                  <select className="form-select form-select-sm" value={txForm.direction}
                    onChange={(e) => setTxForm({ ...txForm, direction: e.target.value })}>
                    <option value="INBOUND">Inbound</option>
                    <option value="OUTBOUND">Outbound</option>
                  </select>
                </div>
                <div className="col-6 col-md-2">
                  <input className="form-control form-control-sm" placeholder="Amount" type="number" min="0" required
                    value={txForm.amount} onChange={(e) => setTxForm({ ...txForm, amount: e.target.value })} />
                </div>
                <div className="col-4 col-md-1">
                  <input className="form-control form-control-sm" placeholder="CUR" maxLength={3}
                    value={txForm.currency} onChange={(e) => setTxForm({ ...txForm, currency: e.target.value.toUpperCase() })} />
                </div>
                <div className="col-8 col-md-2">
                  <select className="form-select form-select-sm" value={txForm.method}
                    onChange={(e) => setTxForm({ ...txForm, method: e.target.value })}>
                    {["SEPA", "SWIFT", "CARD", "CASH", "CRYPTO", "EMONEY", "OTHER"].map((m) =>
                      <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
                <div className="col-6 col-md-2">
                  <input className="form-control form-control-sm" placeholder="Counterparty"
                    value={txForm.counterparty_name} onChange={(e) => setTxForm({ ...txForm, counterparty_name: e.target.value })} />
                </div>
                <div className="col-4 col-md-2">
                  <input className="form-control form-control-sm" placeholder="Country"
                    value={txForm.counterparty_country} onChange={(e) => setTxForm({ ...txForm, counterparty_country: e.target.value })} />
                </div>
                <div className="col-2 col-md-1">
                  <button className="btn btn-sm btn-co w-100" disabled={txBusy}>Add</button>
                </div>
              </form>
            )}
          </div>
        </div>
      </div>
      )}
    </>
  );
};
