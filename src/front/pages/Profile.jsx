import { useEffect, useState } from "react";
import { api } from "../services/api";
import useGlobalReducer from "../hooks/useGlobalReducer";

// One place to manage who you are (identity + photo) and your own account
// security (password, two-factor). The photo is what makes people recognisable
// in chat before they read the name.
export const Profile = () => {
  const { store, dispatch } = useGlobalReducer();
  const [tab, setTab] = useState("identity");
  const [me, setMe] = useState(store.user);
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);

  const refresh = () => api.me().then((d) => {
    setMe(d.user); dispatch({ type: "set_me", payload: d });
  }).catch(() => {});
  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    if (!notice) return undefined;
    const t = setTimeout(() => setNotice(null), 4000);
    return () => clearTimeout(t);
  }, [notice]);

  if (!me) return <div className="empty">Loading…</div>;

  return (
    <>
      <h3 style={{ marginTop: 0 }}>My profile</h3>
      {error && <div className="alert alert-danger py-2">{error}</div>}
      {notice && <div className="alert alert-success py-2"><i className="fa-solid fa-circle-check" /> {notice}</div>}

      <div className="pt-tabs">
        {[["identity", "Identity", "fa-user"],
          ["security", "Security", "fa-shield-halved"]].map(([k, l, i]) => (
          <button key={k} className={"pt-tab" + (tab === k ? " active" : "")}
            onClick={() => setTab(k)}><i className={`fa-solid ${i}`} /> {l}</button>
        ))}
      </div>

      {tab === "identity" && (
        <Identity me={me} onSaved={(m) => { setMe(m); refresh(); setNotice("Profile saved."); }}
          onError={setError} />
      )}
      {tab === "security" && (
        <Security me={me} onChange={refresh} notify={setNotice} onError={setError} />
      )}
    </>
  );
};

const Avatar = ({ me, large }) => {
  const size = large ? 76 : 34;
  if (me.avatar_url) {
    return <img src={me.avatar_url} alt="" className="pf-avatar"
      style={{ width: size, height: size }} />;
  }
  const initials = (me.full_name || me.email || "?").split(" ")
    .map((s) => s[0]).join("").slice(0, 2).toUpperCase();
  return <span className="co-avatar" style={{ width: size, height: size,
    fontSize: large ? "1.6rem" : ".8rem" }}>{initials}</span>;
};

const Identity = ({ me, onSaved, onError }) => {
  const [form, setForm] = useState({
    full_name: me.full_name || "", job_title: me.job_title || "",
    phone: me.phone || "", timezone: me.timezone || "",
  });
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);

  const save = async () => {
    setBusy(true); onError(null);
    try { const r = await api.updateProfile(form); onSaved(r.user); }
    catch (e) { onError(e.message); } finally { setBusy(false); }
  };
  const upload = async (file) => {
    if (!file) return;
    setUploading(true); onError(null);
    try { const r = await api.uploadAvatar(file); onSaved(r.user); }
    catch (e) { onError(e.message); } finally { setUploading(false); }
  };
  const removePhoto = async () => {
    try { const r = await api.removeAvatar(); onSaved(r.user); } catch (e) { onError(e.message); }
  };

  return (
    <div className="co-card">
      <div className="pf-photo-row">
        <Avatar me={me} large />
        <div>
          <label className={"btn btn-sm btn-outline-secondary" + (uploading ? " disabled" : "")}>
            <i className="fa-solid fa-camera" /> {uploading ? "Uploading…" : "Change photo"}
            <input type="file" hidden accept="image/*"
              onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
          {me.avatar_url && (
            <button className="btn btn-sm btn-outline-secondary ms-2" onClick={removePhoto}>Remove</button>
          )}
          <div className="meta" style={{ marginTop: ".35rem" }}>
            A photo helps colleagues recognise you in chat.
          </div>
        </div>
      </div>

      <div className="row g-3 mt-1">
        <div className="col-md-6">
          <label className="form-label">Full name</label>
          <input className="form-control" value={form.full_name}
            onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
        </div>
        <div className="col-md-6">
          <label className="form-label">Job title</label>
          <input className="form-control" value={form.job_title}
            onChange={(e) => setForm({ ...form, job_title: e.target.value })} />
        </div>
        <div className="col-md-6">
          <label className="form-label">Phone</label>
          <input className="form-control" value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })} />
        </div>
        <div className="col-md-6">
          <label className="form-label">Time zone</label>
          <input className="form-control" placeholder="e.g. Europe/Luxembourg"
            value={form.timezone}
            onChange={(e) => setForm({ ...form, timezone: e.target.value })} />
        </div>
        <div className="col-md-6">
          <label className="form-label">Email</label>
          <input className="form-control" value={me.email} disabled />
          <div className="meta">Managed by your administrator.</div>
        </div>
        <div className="col-md-6">
          <label className="form-label">Role</label>
          <input className="form-control" value={me.roles?.join(", ") || me.role} disabled />
        </div>
      </div>
      <button className="btn btn-co mt-3" onClick={save} disabled={busy}>
        {busy ? "Saving…" : "Save"}
      </button>
    </div>
  );
};

const Security = ({ me, onChange, notify, onError }) => {
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [busy, setBusy] = useState(false);
  const [enroll, setEnroll] = useState(null);   // {qr, secret}
  const [code, setCode] = useState("");
  const [backup, setBackup] = useState(null);

  const changePw = async (e) => {
    e.preventDefault(); onError(null);
    if (pw.next !== pw.confirm) { onError("The two passwords do not match."); return; }
    setBusy(true);
    try {
      await api.changePassword(pw.current, pw.next);
      setPw({ current: "", next: "", confirm: "" });
      notify("Password changed.");
    } catch (e2) { onError(e2.message); } finally { setBusy(false); }
  };

  const startEnroll = async () => {
    onError(null);
    try { setEnroll(await api.mfaEnrollSession()); } catch (e) { onError(e.message); }
  };
  const confirmEnroll = async () => {
    onError(null);
    try {
      const r = await api.mfaConfirmSession(code);
      setBackup(r.backup_codes); setEnroll(null); setCode("");
      onChange();
    } catch (e) { onError(e.message); }
  };
  const disable2fa = async () => {
    try { await api.mfaDisable(); notify("Two-factor turned off."); onChange(); }
    catch (e) { onError(e.message); }
  };

  return (
    <>
      <div className="co-card">
        <div className="section-title">Password</div>
        <form onSubmit={changePw} style={{ maxWidth: 420 }}>
          <label className="form-label">Current password</label>
          <input className="form-control" type="password" value={pw.current}
            onChange={(e) => setPw({ ...pw, current: e.target.value })} required />
          <label className="form-label mt-2">New password</label>
          <input className="form-control" type="password" value={pw.next}
            onChange={(e) => setPw({ ...pw, next: e.target.value })} required minLength={12} />
          <label className="form-label mt-2">Confirm new password</label>
          <input className="form-control" type="password" value={pw.confirm}
            onChange={(e) => setPw({ ...pw, confirm: e.target.value })} required />
          <button className="btn btn-co mt-3" disabled={busy}>Change password</button>
        </form>
      </div>

      <div className="co-card">
        <div className="section-title">Two-factor authentication</div>
        {backup ? (
          /* Shown once, right after enrolment — before the plain ON row,
             because onChange() has already flipped me.mfa_enabled. */
          <>
            <div className="alert alert-success py-2">Two-factor is on. Save these backup codes somewhere safe — each works once.</div>
            <div className="pf-backup">
              {backup.map((c) => <code key={c}>{c}</code>)}
            </div>
            <button className="btn btn-sm btn-outline-secondary mt-2"
              onClick={() => setBackup(null)}>I saved them</button>
          </>
        ) : me.mfa_enabled ? (
          <div className="d-flex align-items-center justify-content-between">
            <div>
              <span className="chip LOW">ON</span>{" "}
              <span className="muted">Method: {me.mfa_method === "TOTP" ? "Authenticator app" : "Emailed code"}</span>
            </div>
            {me.mfa_method === "TOTP" && (
              <button className="btn btn-sm btn-outline-danger" onClick={disable2fa}>Turn off</button>
            )}
          </div>
        ) : enroll ? (
          <div style={{ maxWidth: 320 }}>
            <p className="muted" style={{ fontSize: ".85rem" }}>
              Scan with an authenticator app, then enter the code it shows.
            </p>
            <div style={{ textAlign: "center" }} dangerouslySetInnerHTML={{ __html: enroll.qr_svg }} />
            <div className="meta" style={{ wordBreak: "break-all", textAlign: "center" }}>
              Key: <code>{enroll.secret}</code>
            </div>
            <input className="form-control mt-2" inputMode="numeric" placeholder="123456"
              value={code} onChange={(e) => setCode(e.target.value)} />
            <button className="btn btn-co mt-2" disabled={code.trim().length < 6}
              onClick={confirmEnroll}>Turn on</button>
          </div>
        ) : (
          <>
            <p className="muted" style={{ fontSize: ".88rem" }}>
              Add a second step at sign-in with an authenticator app.
            </p>
            <button className="btn btn-co" onClick={startEnroll}>
              <i className="fa-solid fa-shield-halved" /> Set up two-factor
            </button>
          </>
        )}
      </div>

      <div className="co-card">
        <div className="section-title">Account</div>
        <div className="meta">
          Email {me.email_verified
            ? <span className="chip LOW">verified</span>
            : <span className="chip HIGH">unverified</span>}
          {me.last_login_at && ` · last sign-in ${new Date(me.last_login_at).toLocaleString()}`}
        </div>
      </div>
    </>
  );
};
