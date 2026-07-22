import { useEffect, useState } from "react";
import { api } from "../services/api";

// How a customer gets their portal account: the staff member enters an email,
// the client receives a Register button and a QR code leading to the same
// token-bound page. The modal also shows the link and QR directly, for the
// client sitting across the desk who would rather scan the officer's screen.
export const PortalAccess = ({ customerId, onClose }) => {
  const [data, setData] = useState(null);
  const [email, setEmail] = useState("");
  const [created, setCreated] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  const load = () => api.portalAccess(customerId).then(setData)
    .catch((e) => setError(e.message));
  // Not useEffect(load, …): load returns a promise, which React would take
  // for a cleanup function and crash on unmount.
  useEffect(() => { load(); }, [customerId]);

  const invite = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const res = await api.invitePortal(customerId, email.trim());
      setCreated(res); setEmail("");
      load();
    } catch (e2) { setError(e2.message); }
    finally { setBusy(false); }
  };

  const revoke = async (iid) => {
    try { await api.revokePortalInvite(customerId, iid); setCreated(null); load(); }
    catch (e2) { setError(e2.message); }
  };

  const copy = (link) => {
    navigator.clipboard?.writeText(link).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 1800);
    });
  };

  const Invite = ({ inv }) => (
    <div className="pa-invite">
      <div className="pa-qr" dangerouslySetInnerHTML={{ __html: inv.qr_svg }} />
      <div className="grow">
        <div className="title">{inv.email}</div>
        <div className="meta">
          Invitation sent · expires {new Date(inv.expires_at).toLocaleDateString()}
        </div>
        <div className="pa-link">{inv.link}</div>
        <div className="d-flex gap-2" style={{ marginTop: ".45rem" }}>
          <button className="btn btn-sm btn-outline-secondary"
            onClick={() => copy(inv.link)}>
            <i className="fa-solid fa-copy" /> {copied ? "Copied!" : "Copy link"}
          </button>
          <button className="btn btn-sm btn-outline-danger"
            onClick={() => revoke(inv.id)}>Revoke</button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="cd-backdrop" onClick={onClose}>
      <div className="co-card cd-modal" onClick={(e) => e.stopPropagation()}>
        <h4 style={{ marginTop: 0 }}>
          <i className="fa-solid fa-user-shield" /> Portal access
        </h4>
        <p className="muted" style={{ fontSize: ".85rem" }}>
          The client registers through the link or the QR code — the account is
          bound to this file by the invitation itself.
        </p>

        {error && <div className="alert alert-danger py-2">{error}</div>}
        {!data && !error && <div className="empty">Loading…</div>}

        {data && data.accounts.length > 0 && (
          <>
            <div className="section-title">Can already sign in</div>
            {data.accounts.map((a) => (
              <div className="work-row" key={a.id}>
                <span className={`dotsev ${a.is_active ? "LOW" : "HIGH"}`} />
                <div className="grow">
                  <div className="title">{a.full_name || a.email}</div>
                  <div className="meta">{a.email}</div>
                </div>
                <span className={`chip ${a.is_active ? "LOW" : "HIGH"}`}>
                  {a.is_active ? "ACTIVE" : "DISABLED"}
                </span>
              </div>
            ))}
          </>
        )}

        {created && (
          <>
            <div className="section-title" style={{ marginTop: ".8rem" }}>
              Invitation created
            </div>
            {created.email?.sent === false && (
              <div className="alert alert-warning py-2" style={{ fontSize: ".82rem" }}>
                The email could not be sent ({created.email.reason}). Share the
                link or the QR code directly.
              </div>
            )}
            {created.email?.sent && (
              <div className="alert alert-success py-2" style={{ fontSize: ".82rem" }}>
                <i className="fa-solid fa-envelope-circle-check" /> Email sent.
              </div>
            )}
            <Invite inv={{ ...created.invitation, link: created.link,
                           qr_svg: created.qr_svg }} />
          </>
        )}

        {data && !created && data.pending.length > 0 && (
          <>
            <div className="section-title" style={{ marginTop: ".8rem" }}>
              Pending invitation
            </div>
            {data.pending.map((inv) => <Invite key={inv.id} inv={inv} />)}
          </>
        )}

        <form onSubmit={invite} style={{ marginTop: "1rem" }}>
          <label className="cd-label">Client email</label>
          <div className="d-flex gap-2">
            <input className="form-control form-control-sm" type="email"
              placeholder="client@example.com" value={email}
              onChange={(e) => setEmail(e.target.value)} required />
            <button className="btn btn-sm btn-co" disabled={busy || !email.trim()}>
              {busy ? "Sending…" : "Invite"}
            </button>
          </div>
        </form>

        <div className="cd-actions" style={{ marginTop: "1rem" }}>
          <button className="btn btn-sm btn-outline-secondary"
            onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
};
