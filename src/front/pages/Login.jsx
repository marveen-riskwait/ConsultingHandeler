import { useState } from "react";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";

export const Login = () => {
  const { dispatch } = useGlobalReducer();
  // An invitation link (?invite=TOKEN) switches the screen to accept mode.
  const inviteToken = new URLSearchParams(window.location.search).get("invite");
  const [mode, setMode] = useState(inviteToken ? "invite" : "login");
  const [email, setEmail] = useState(inviteToken ? "" : "analyst@demo.io");
  const [password, setPassword] = useState(inviteToken ? "" : "demo1234");
  const [fullName, setFullName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const [sentReset, setSentReset] = useState(false);
  // Second-factor step after the password: a pending ticket + what to show.
  const [mfa, setMfa] = useState(null);   // {ticket, method} | {ticket, setup, qr, secret}
  const [code, setCode] = useState("");

  const submitForgot = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await api.forgotPassword(email); setSentReset(true); }
    catch (e2) { setError(e2.message); }
    finally { setBusy(false); }
  };

  const submitCode = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const data = mfa.setup
        ? await api.mfaConfirm(mfa.ticket, code)
        : await api.mfaVerify(mfa.ticket, code);
      dispatch({ type: "login", payload: data });
    } catch (e2) { setError(e2.message); }
    finally { setBusy(false); }
  };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      let data;
      if (mode === "invite") {
        data = await api.acceptInvitation({ token: inviteToken, password, full_name: fullName });
        window.history.replaceState({}, "", "/");
      } else if (mode === "login") {
        data = await api.login(email, password);
        if (data.mfa_required) { setMfa({ ticket: data.ticket, method: data.method }); return; }
        if (data.mfa_setup_required) {
          const enr = await api.mfaEnroll(data.ticket);
          setMfa({ ticket: data.ticket, setup: true, qr: enr.qr_svg, secret: enr.secret });
          return;
        }
      } else {
        data = await api.register({ email, password, full_name: fullName, organization_name: orgName });
      }
      dispatch({ type: "login", payload: data });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (mfa) {
    return (
      <div className="login-wrap">
        <form className="login-card" onSubmit={submitCode}>
          <h4 style={{ marginTop: 0 }}>
            {mfa.setup ? "Set up two-factor authentication"
              : "Two-factor authentication"}
          </h4>
          {error && <div className="alert alert-danger py-2">{error}</div>}
          {mfa.setup ? (
            <>
              <p className="muted" style={{ fontSize: ".85rem" }}>
                Scan this with an authenticator app (Google Authenticator, 1Password…),
                then enter the 6-digit code it shows.
              </p>
              <div style={{ textAlign: "center" }}
                dangerouslySetInnerHTML={{ __html: mfa.qr }} />
              <div className="muted" style={{ fontSize: ".72rem", wordBreak: "break-all",
                textAlign: "center", marginTop: ".3rem" }}>
                Can't scan? Key: <code>{mfa.secret}</code>
              </div>
            </>
          ) : mfa.method === "EMAIL_OTP" ? (
            <p className="muted" style={{ fontSize: ".85rem" }}>
              We emailed you a one-time code. Enter it below.
            </p>
          ) : (
            <p className="muted" style={{ fontSize: ".85rem" }}>
              Enter the 6-digit code from your authenticator app.
            </p>
          )}
          <input className="form-control" inputMode="numeric" autoFocus
            placeholder="123456" value={code}
            onChange={(e) => setCode(e.target.value)} />
          <button className="btn btn-co mt-3" disabled={busy || code.trim().length < 6}>
            {busy ? "Checking…" : mfa.setup ? "Turn on & continue" : "Verify"}
          </button>
          <div className="text-center mt-3" style={{ fontSize: ".85rem" }}>
            <a href="#" onClick={(e) => { e.preventDefault(); setMfa(null); setCode(""); setError(null); }}>Back to sign in</a>
          </div>
        </form>
      </div>
    );
  }

  if (mode === "forgot") {
    return (
      <div className="login-wrap">
        <form className="login-card" onSubmit={submitForgot}>
          <h4 style={{ marginTop: 0 }}>Reset your password</h4>
          {sentReset ? (
            <>
              <p className="muted" style={{ fontSize: ".88rem" }}>
                If an account exists for that address, a reset link is on its
                way. Check your inbox.
              </p>
              <a href="#" onClick={(e) => { e.preventDefault(); setMode("login"); setSentReset(false); }}>Back to sign in</a>
            </>
          ) : (
            <>
              <p className="muted" style={{ fontSize: ".85rem" }}>
                Enter your email and we will send you a reset link.
              </p>
              {error && <div className="alert alert-danger py-2">{error}</div>}
              <label className="form-label">Email</label>
              <input className="form-control" type="email" value={email}
                onChange={(e) => setEmail(e.target.value)} required />
              <button className="btn btn-co mt-3" disabled={busy}>
                {busy ? "Sending…" : "Send reset link"}
              </button>
              <div className="text-center mt-3" style={{ fontSize: ".85rem" }}>
                <a href="#" onClick={(e) => { e.preventDefault(); setMode("login"); }}>Back to sign in</a>
              </div>
            </>
          )}
        </form>
      </div>
    );
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: ".25rem" }}>
          <span className="dot" style={{ width: 12, height: 12, borderRadius: "50%", background: "var(--co-primary)" }} />
          <h4 style={{ margin: 0 }}>Compliance OS</h4>
        </div>
        <p className="muted" style={{ fontSize: ".85rem" }}>
          {mode === "login" ? "Sign in to your compliance workspace"
            : mode === "invite" ? "You have been invited — set up your account"
            : "Create a new organization"}
        </p>

        {mode === "invite" && (
          <>
            <label className="form-label mt-2">Full name</label>
            <input className="form-control" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
          </>
        )}

        {mode === "register" && (
          <>
            <label className="form-label mt-2">Full name</label>
            <input className="form-control" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
            <label className="form-label mt-2">Organization</label>
            <input className="form-control" value={orgName} onChange={(e) => setOrgName(e.target.value)} required />
          </>
        )}

        {mode !== "invite" && (
          <>
            <label className="form-label mt-2">Email</label>
            <input type="email" className="form-control" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </>
        )}

        <label className="form-label mt-2">{mode === "invite" ? "Choose a password" : "Password"}</label>
        <input type="password" className="form-control" value={password} onChange={(e) => setPassword(e.target.value)} required />

        {error && <div className="alert alert-danger py-2 mt-3 mb-0" style={{ fontSize: ".85rem" }}>{error}</div>}

        <button className="btn btn-co w-100 mt-3" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in"
            : mode === "invite" ? "Join organization" : "Create workspace"}
        </button>

        <div className="text-center mt-3" style={{ fontSize: ".85rem" }}>
          {mode === "login" ? (
            <>
              <span className="muted">New here? <a href="#" onClick={(e) => { e.preventDefault(); setMode("register"); }}>Create an organization</a></span>
              <div className="mt-1">
                <a href="#" onClick={(e) => { e.preventDefault(); setMode("forgot"); setError(null); }}>Forgot your password?</a>
              </div>
            </>
          ) : (
            <span className="muted">Have an account? <a href="#" onClick={(e) => { e.preventDefault(); setMode("login"); }}>Sign in</a></span>
          )}
        </div>

        {mode === "login" && (
          <div className="mt-3 p-2" style={{ background: "#f4f5f9", borderRadius: 8, fontSize: ".78rem" }}>
            <b>Demo:</b> analyst@demo.io · officer@demo.io — password <code>demo1234</code><br />
            <span className="muted">Run <code>flask seed-demo</code> to create them.</span>
          </div>
        )}
      </form>
    </div>
  );
};
