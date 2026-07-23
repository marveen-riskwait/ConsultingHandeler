import { useState } from "react";
import { api } from "../services/api";

// Public page reached from the emailed link (/reset-password?token=...).
export const ResetPassword = () => {
  const token = new URLSearchParams(window.location.search).get("token");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (password !== confirm) { setError("The two passwords do not match."); return; }
    setBusy(true); setError(null);
    try { await api.resetPassword(token, password); setDone(true); }
    catch (e2) { setError(e2.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="auth-shell">
      <div className="co-card auth-card">
        <h4><span className="pt-dot" /> Reset your password</h4>
        {done ? (
          <>
            <p><b>Your password has been changed.</b></p>
            <a className="btn btn-co" href="/login">Sign in</a>
          </>
        ) : (
          <form onSubmit={submit}>
            {error && <div className="alert alert-danger py-2">{error}</div>}
            {!token && <div className="alert alert-danger py-2">Missing reset token.</div>}
            <label className="form-label">New password</label>
            <input className="form-control" type="password" value={password}
              onChange={(e) => setPassword(e.target.value)} required minLength={12} />
            <label className="form-label mt-2">Confirm new password</label>
            <input className="form-control" type="password" value={confirm}
              onChange={(e) => setConfirm(e.target.value)} required />
            <button className="btn btn-co mt-3" disabled={busy || !token}>
              {busy ? "Saving…" : "Set new password"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
};
