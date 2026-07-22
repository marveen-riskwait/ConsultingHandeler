import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { Chat } from "./Chat";
import { resetSocket } from "../services/socket";

// The customer's whole experience. A separate shell rather than the staff one
// with items hidden: a client should never be one CSS rule away from an
// analyst's screen. Everything here reads /api/portal/*, which cannot return
// risk, screening or any other assessment.

const Progress = ({ progress }) => {
  const { requested = 0, provided = 0 } = progress || {};
  const pct = requested ? Math.round((provided / requested) * 100) : 0;
  return (
    <div className="pt-progress">
      <div className="pt-progress-head">
        <b>{provided} of {requested} provided</b>
        <span className="muted">{pct}%</span>
      </div>
      <div className="pt-bar"><div className="pt-bar-fill" style={{ width: `${pct}%` }} /></div>
    </div>
  );
};

const Field = ({ spec, value, onChange }) => {
  const v = value ?? "";
  if (spec.type === "textarea") {
    return <textarea className="form-control" rows={3} value={v}
      onChange={(e) => onChange(spec.key, e.target.value)} />;
  }
  if (spec.type === "select") {
    return (
      <select className="form-select" value={v}
        onChange={(e) => onChange(spec.key, e.target.value)}>
        <option value="">—</option>
        {(spec.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  }
  return <input className="form-control" type={spec.type === "date" ? "date" : "text"}
    value={v} onChange={(e) => onChange(spec.key, e.target.value)} />;
};

// ----------------------------------------------------------------- my details
const MyDetails = ({ data, reload, notify }) => {
  const [values, setValues] = useState({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setValues(Object.fromEntries(
      Object.entries(data.values || {}).map(([k, f]) => [k, f.value ?? ""])));
  }, [data]);

  const save = async () => {
    setBusy(true);
    try {
      const res = await api.portalSaveForm(values);
      notify(`Saved — thank you. ${res.saved} answer${res.saved === 1 ? "" : "s"} recorded.`);
      reload();
    } catch (e) { notify(e.message, true); }
    finally { setBusy(false); }
  };

  return (
    <>
      <p className="muted">
        Please tell us about yourself. You can save as you go and come back later.
      </p>
      {(data.sections || []).map((section) => (
        <div className="co-card" key={section.key}>
          <div className="section-title">{section.label}</div>
          {section.description && <p className="muted" style={{ fontSize: ".85rem" }}>{section.description}</p>}
          <div className="row g-3">
            {(section.fields || []).map((spec) => (
              <div className="col-md-6" key={spec.key}>
                <label className="form-label">
                  {spec.label} {spec.required && <span style={{ color: "var(--sev-critical)" }}>*</span>}
                </label>
                <Field spec={spec} value={values[spec.key]}
                  onChange={(k, val) => setValues((p) => ({ ...p, [k]: val }))} />
                {spec.help && <div className="meta">{spec.help}</div>}
              </div>
            ))}
          </div>
        </div>
      ))}
      <button className="btn btn-co" onClick={save} disabled={busy}>
        {busy ? "Saving…" : "Save my answers"}
      </button>
    </>
  );
};

// ------------------------------------------------------------------ documents
const MyDocuments = ({ data, reload, notify }) => {
  const [uploading, setUploading] = useState(null);
  const [note, setNote] = useState({});
  const outstanding = (data.progress?.outstanding || [])
    .filter((o) => o.kind === "DOCUMENT");
  const docs = data.documents || [];

  const send = async (docType, file) => {
    if (!file) return;
    setUploading(docType);
    try {
      await api.portalUploadDocument(docType, note[docType] || "", file);
      setNote((n) => ({ ...n, [docType]: "" }));
      notify(`${file.name} sent — thank you.`);
      reload();
    } catch (e) { notify(e.message, true); }
    finally { setUploading(null); }
  };

  const withdraw = async (doc) => {
    try { await api.portalDeleteDocument(doc.id); notify("Document withdrawn."); reload(); }
    catch (e) { notify(e.message, true); }
  };

  const STATE = {
    RECEIVED: ["INFO", "Received — we are looking at it"],
    ACCEPTED: ["LOW", "Accepted"],
    RETURNED: ["HIGH", "Please send it again"],
  };

  return (
    <>
      <div className="co-card">
        <div className="section-title">What we still need</div>
        {outstanding.length === 0 && (
          <div className="empty">Nothing outstanding — thank you.</div>
        )}
        {outstanding.map((o) => (
          <div className="work-row" key={o.code}>
            <span className="dotsev HIGH" />
            <div className="grow">
              <div className="title">{o.label}</div>
              <input className="form-control form-control-sm" style={{ marginTop: ".35rem" }}
                placeholder="Tell us what you are sending (optional)"
                value={note[o.code] || ""}
                onChange={(e) => setNote((n) => ({ ...n, [o.code]: e.target.value }))} />
            </div>
            <label className={"btn btn-sm btn-co" + (uploading === o.code ? " disabled" : "")}>
              <i className="fa-solid fa-arrow-up-from-bracket" />{" "}
              {uploading === o.code ? "Sending…" : "Send"}
              <input type="file" hidden accept=".pdf,.png,.jpg,.jpeg,.heic,.webp"
                onChange={(e) => { send(o.code, e.target.files?.[0]); e.target.value = ""; }} />
            </label>
          </div>
        ))}
      </div>

      <div className="co-card">
        <div className="section-title">What you have sent</div>
        {docs.length === 0 && <div className="empty">Nothing sent yet.</div>}
        {docs.map((d) => {
          const [chip, text] = STATE[d.state] || ["INFO", d.state];
          return (
            <div className="work-row" key={d.id}>
              <span className={`dotsev ${chip === "LOW" ? "LOW" : chip === "HIGH" ? "HIGH" : "INFO"}`} />
              <div className="grow">
                <div className="title">{d.file_name || d.doc_type}</div>
                <div className="meta">{d.doc_type}{d.description ? ` · ${d.description}` : ""}</div>
                {d.returned_reason && (
                  <div className="meta" style={{ color: "var(--sev-high)" }}>
                    {d.returned_reason}
                  </div>
                )}
              </div>
              <span className={`chip ${chip}`}>{text}</span>
              {d.state !== "ACCEPTED" && (
                <button className="btn btn-sm btn-outline-secondary"
                  onClick={() => withdraw(d)}>Withdraw</button>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
};

// ------------------------------------------------------------------ assistant
const Helper = ({ notify }) => {
  const [state, setState] = useState({ messages: [], suggested: [] });
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.portalAssistant().then(setState).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);

  const send = async (text) => {
    const message = (text ?? draft).trim();
    if (!message) return;
    setBusy(true); setDraft("");
    try { await api.portalAsk(message); await load(); }
    catch (e) { notify(e.message, true); }
    finally { setBusy(false); }
  };

  return (
    <div className="co-card">
      <div className="section-title">Ask for help</div>
      <p className="muted" style={{ fontSize: ".85rem" }}>
        This assistant can explain what we still need and how to provide it. For
        anything about your application itself, send a message to the team.
      </p>
      <div className="pt-thread">
        {state.messages.length === 0 && (
          <div className="pt-suggest">
            {(state.suggested || []).map((s) => (
              <button key={s} className="btn btn-sm btn-outline-secondary"
                onClick={() => send(s)}>{s}</button>
            ))}
          </div>
        )}
        {state.messages.map((m) => (
          <div key={m.id} className={"pt-msg " + (m.role === "user" ? "mine" : "")}>
            {m.content}
          </div>
        ))}
      </div>
      <div className="d-flex gap-2" style={{ marginTop: ".6rem" }}>
        <input className="form-control" value={draft} placeholder="Type your question…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()} />
        <button className="btn btn-co" onClick={() => send()} disabled={busy}>
          {busy ? "…" : "Ask"}
        </button>
      </div>
    </div>
  );
};

// ----------------------------------------------------------------------- page
export const Portal = () => {
  const { store, dispatch } = useGlobalReducer();
  const [data, setData] = useState(null);
  const [tab, setTab] = useState("details");
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const reload = useCallback(() => {
    api.portalForm().then(setData).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    if (!notice) return undefined;
    const t = setTimeout(() => setNotice(null), 5000);
    return () => clearTimeout(t);
  }, [notice]);

  const notify = (msg, isError) => (isError ? setError(msg) : setNotice(msg));

  const submit = async () => {
    setBusy(true); setError(null);
    try { await api.portalSubmit(); notify("Thank you — your file has been sent to our team."); reload(); }
    catch (e) { notify(e.message, true); }
    finally { setBusy(false); }
  };
  const reopen = async () => {
    setBusy(true); setError(null);
    try { await api.portalReopen(); notify("Your file is open again — you can make changes."); reload(); }
    catch (e) { notify(e.message, true); }
    finally { setBusy(false); }
  };
  const logout = () => { resetSocket(); dispatch({ type: "logout" }); };

  const tabs = [
    ["details", "My information", "fa-user-pen"],
    ["documents", "My documents", "fa-file-arrow-up"],
    ["messages", "Messages", "fa-comments"],
    ["help", "Help", "fa-circle-question"],
  ];

  return (
    <div className="pt-shell">
      <header className="pt-top">
        <div className="pt-brand">
          <span className="pt-dot" /> {data?.organization || "Compliance OS"}
        </div>
        <div className="d-flex align-items-center gap-3">
          <span className="muted">{store.user?.full_name || store.user?.email}</span>
          <button className="btn btn-sm btn-outline-secondary" onClick={logout}>
            <i className="fa-solid fa-right-from-bracket" /> Log out
          </button>
        </div>
      </header>

      <main className="pt-main">
        <h3 style={{ marginTop: 0 }}>Your onboarding</h3>
        <p className="muted">
          Everything we need from you is here. Your answers are private to you
          and our compliance team.
        </p>
        {data && <Progress progress={data.progress} />}
        {data && (
          <div className="pt-submit">
            {data.customer?.submitted ? (
              <>
                <div>
                  <b>Your file has been submitted.</b>
                  <div className="muted" style={{ fontSize: ".85rem" }}>
                    Our team will come back to you. You can still take it back
                    to correct something, until someone starts reviewing it.
                  </div>
                </div>
                <button className="btn btn-sm btn-outline-secondary"
                  onClick={reopen} disabled={busy}>
                  <i className="fa-solid fa-rotate-left" /> Take it back
                </button>
              </>
            ) : (
              <>
                <div>
                  <b>Not submitted yet.</b>
                  <div className="muted" style={{ fontSize: ".85rem" }}>
                    Send it to us when you are ready — you can still add things
                    afterwards if we have not started.
                  </div>
                </div>
                <button className="btn btn-sm btn-co" onClick={submit} disabled={busy}>
                  <i className="fa-solid fa-paper-plane" /> Submit my file
                </button>
              </>
            )}
          </div>
        )}

        {error && <div className="alert alert-danger py-2">{error}</div>}
        {notice && (
          <div className="alert alert-success py-2">
            <i className="fa-solid fa-circle-check" /> {notice}
          </div>
        )}

        <div className="pt-tabs">
          {tabs.map(([key, label, icon]) => (
            <button key={key} className={"pt-tab" + (tab === key ? " active" : "")}
              onClick={() => setTab(key)}>
              <i className={`fa-solid ${icon}`} /> {label}
            </button>
          ))}
        </div>

        {!data && !error && <div className="empty">Loading…</div>}
        {data && tab === "details" && (
          <MyDetails data={data} reload={reload} notify={notify} />
        )}
        {data && tab === "documents" && (
          <MyDocuments data={data} reload={reload} notify={notify} />
        )}
        {tab === "messages" && <Chat />}
        {tab === "help" && <Helper notify={notify} />}
      </main>
    </div>
  );
};
