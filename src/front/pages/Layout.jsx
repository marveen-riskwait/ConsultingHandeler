import { useEffect } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import ScrollToTop from "../components/ScrollToTop";
import { Sidebar } from "../components/Sidebar";
import { Login } from "./Login";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";

// The whole app is gated: without a session we show the Login screen (no shell).
// Once authenticated we render the dark sidebar + a slim topbar around the page.
export const Layout = () => {
  const { store, dispatch } = useGlobalReducer();
  const navigate = useNavigate();

  useEffect(() => {
    if (store.token && !store.organization) {
      api.me()
        .then((data) => dispatch({ type: "set_me", payload: data }))
        .catch(() => dispatch({ type: "logout" }));
    }
  }, [store.token]);

  if (!store.token) {
    return <Login />;
  }

  const logout = () => { dispatch({ type: "logout" }); navigate("/"); };
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
