import { NavLink, useNavigate } from "react-router-dom";
import useGlobalReducer from "../hooks/useGlobalReducer";

export const Navbar = () => {
  const { store, dispatch } = useGlobalReducer();
  const navigate = useNavigate();

  const logout = () => {
    dispatch({ type: "logout" });
    navigate("/");
  };

  return (
    <nav className="co-nav">
      <div style={{ display: "flex", alignItems: "center", gap: "1.25rem" }}>
        <NavLink to="/" className="brand">
          <span className="dot" /> Compliance OS
        </NavLink>
        <NavLink to="/" end className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
          <i className="fa-solid fa-inbox" /> My Work
        </NavLink>
        <NavLink to="/customers" className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
          <i className="fa-solid fa-users" /> Customers
        </NavLink>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: ".9rem" }}>
        {store.user && (
          <span style={{ fontSize: ".85rem", color: "#c7cbd6" }}>
            {store.user.full_name} · <b style={{ color: "#fff" }}>{store.user.role}</b>
          </span>
        )}
        <button className="btn btn-sm btn-outline-light" onClick={logout}>
          <i className="fa-solid fa-right-from-bracket" /> Logout
        </button>
      </div>
    </nav>
  );
};
