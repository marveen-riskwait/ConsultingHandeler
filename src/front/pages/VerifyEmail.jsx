import { useEffect, useState } from "react";
import { api } from "../services/api";

// Public page reached from the emailed link (/verify-email?token=...). It
// confirms the address, then points the user to sign in.
export const VerifyEmail = () => {
  const token = new URLSearchParams(window.location.search).get("token");
  const [state, setState] = useState("checking"); // checking | ok | error

  useEffect(() => {
    if (!token) { setState("error"); return; }
    api.verifyEmail(token).then(() => setState("ok")).catch(() => setState("error"));
  }, [token]);

  return (
    <div className="auth-shell">
      <div className="co-card auth-card">
        <h4><span className="pt-dot" /> Compliance OS</h4>
        {state === "checking" && <p className="muted">Confirming your email…</p>}
        {state === "ok" && (
          <>
            <p><b>Your email is confirmed.</b></p>
            <a className="btn btn-co" href="/login">Sign in</a>
          </>
        )}
        {state === "error" && (
          <>
            <div className="alert alert-danger py-2">
              This link is invalid or has expired.
            </div>
            <a className="btn btn-outline-secondary" href="/login">Back to sign in</a>
          </>
        )}
      </div>
    </div>
  );
};
