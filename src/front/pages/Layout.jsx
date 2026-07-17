import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import ScrollToTop from "../components/ScrollToTop";
import { Navbar } from "../components/Navbar";
import { Login } from "./Login";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { api } from "../services/api";

// The whole app is gated: without a session we show the Login screen (no
// navbar). Once authenticated we hydrate the current user and render the app.
export const Layout = () => {
  const { store, dispatch } = useGlobalReducer();

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

  return (
    <ScrollToTop>
      <div className="co-app">
        <Navbar />
        <div className="co-container">
          <Outlet />
        </div>
      </div>
    </ScrollToTop>
  );
};
