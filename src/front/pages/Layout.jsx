import { useEffect } from "react";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import ScrollToTop from "../components/ScrollToTop";
import { Sidebar } from "../components/Sidebar";
import { Login } from "./Login";
import { Landing } from "./Landing";
import { Portal } from "./Portal";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";
import { resetSocket } from "../services/socket";

// The whole app is gated: logged-out visitors get the public landing page at
// the root (any other path shows the Login screen — e.g. /login, invite links).
// Once authenticated we render the dark sidebar + a slim topbar around the page.
export const Layout = () => {
  const { store, dispatch } = useGlobalReducer();
  const navigate = useNavigate();
  const location = useLocation();

  // Refresh the profile (roles/permissions) on load AND on every navigation,
  // so a permission granted by an admin mid-session shows up in the UI without
  // re-login — the sidebar and buttons are all driven by user.permissions.
  useEffect(() => {
    if (store.token) {
      api.me()
        .then((data) => dispatch({ type: "set_me", payload: data }))
        .catch(() => dispatch({ type: "logout" }));
    }
  }, [store.token, location.pathname]);

  // Also refresh when the tab regains focus (e.g. the admin granted a
  // permission while the user was on another window).
  useEffect(() => {
    const onFocus = () => {
      if (store.token) {
        api.me()
          .then((data) => dispatch({ type: "set_me", payload: data }))
          .catch(() => {});
      }
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [store.token]);

  if (!store.token) {
    // Invitation links (?invite=TOKEN) must reach the accept screen, never the
    // marketing page — including legacy links that point at the root.
    const isInvite = new URLSearchParams(location.search).has("invite");
    return location.pathname === "/" && !isInvite ? <Landing /> : <Login />;
  }

  // A customer gets their own shell, whatever the URL says. Not the staff
  // layout with items filtered out: a client should never be one CSS rule or
  // one stale permission away from an analyst's screen.
  if (store.user?.is_portal_user) {
    return <ScrollToTop><Portal /></ScrollToTop>;
  }

  const logout = () => { resetSocket(); dispatch({ type: "logout" }); navigate("/"); };
  const initials = (store.user?.full_name || store.user?.email || "?")
    .split(" ").map((s) => s[0]).join("").slice(0, 2).toUpperCase();

  return (
    <ScrollToTop>
      <div className="co-app">
        <Sidebar />
        <div className="co-main">
          <header className="co-topbar">
            <div className="co-user">
              <span className="co-avatar">{initials}</span>
              <span className="co-user-name">{store.user?.full_name}</span>
            </div>
            <button className="btn btn-sm btn-outline-secondary" onClick={logout}>
              <i className="fa-solid fa-right-from-bracket" /> Logout
            </button>
          </header>
          <div className="co-content">
            <Outlet />
          </div>
        </div>
      </div>
    </ScrollToTop>
  );
};
