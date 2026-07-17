import { useState } from "react";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";

export const Login = () => {
  const { dispatch } = useGlobalReducer();
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("analyst@demo.io");
  const [password, setPassword] = useState("demo1234");
  const [fullName, setFullName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const data = mode === "login"
        ? await api.login(email, password)
        : await api.register({ email, password, full_name: fullName, organization_name: orgName });
      dispatch({ type: "login", payload: data });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: ".25rem" }}>
          <span className="dot" style={{ width: 12, height: 12, borderRadius: "50%", background: "var(--co-primary)" }} />
          <h4 style={{ margin: 0 }}>Compliance OS</h4>
        </div>
        <p className="muted" style={{ fontSize: ".85rem" }}>
          {mode === "login" ? "Sign in to your compliance workspace" : "Create a new organization"}
        </p>

        {mode === "register" && (
          <>
            <label className="form-label mt-2">Full name</label>
            <input className="form-control" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
            <label className="form-label mt-2">Organization</label>
            <input className="form-control" value={orgName} onChange={(e) => setOrgName(e.target.value)} required />
          </>
        )}

        <label className="form-label mt-2">Email</label>
        <input type="email" className="form-control" value={email} onChange={(e) => setEmail(e.target.value)} required />

        <label className="form-label mt-2">Password</label>
        <input type="password" className="form-control" value={password} onChange={(e) => setPassword(e.target.value)} required />

        {error && <div className="alert alert-danger py-2 mt-3 mb-0" style={{ fontSize: ".85rem" }}>{error}</div>}

        <button className="btn btn-co w-100 mt-3" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create workspace"}
        </button>

        <div className="text-center mt-3" style={{ fontSize: ".85rem" }}>
          {mode === "login" ? (
            <span className="muted">New here? <a href="#" onClick={(e) => { e.preventDefault(); setMode("register"); }}>Create an organization</a></span>
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
