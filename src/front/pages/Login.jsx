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

  const submitForgot = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await api.forgotPassword(email); setSentReset(true); }
    catch (e2) { setError(e2.message); }
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
