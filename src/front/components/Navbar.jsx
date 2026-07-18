import { NavLink, useNavigate } from "react-router-dom";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { filterByPermission } from "../permissions/can";

// Navigation is generated from permissions. The backend still enforces access;
// this only hides what the user can't use.
const NAV_ITEMS = [
  { to: "/", end: true, icon: "fa-inbox", label: "My Work", permission: "workspace.view" },
  { to: "/customers", icon: "fa-users", label: "Customers", permission: "customer.view" },
  { to: "/management", icon: "fa-chart-line", label: "Management", permission: "management.view" },
  { to: "/administration", icon: "fa-gear", label: "Admin", permission: "user.view" },
];

export const Navbar = () => {
  const { store, dispatch } = useGlobalReducer();
  const navigate = useNavigate();

  const logout = () => {
    dispatch({ type: "logout" });
    navigate("/");
  };

  const items = filterByPermission(NAV_ITEMS, store.user);

  return (
    <nav className="co-nav">
      <div style={{ display: "flex", alignItems: "center", gap: "1.25rem" }}>
        <NavLink to="/" className="brand">
          <span className="dot" /> Compliance OS
        </NavLink>
        {items.map((it) => (
          <NavLink key={it.to} to={it.to} end={it.end}
            className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
            <i className={`fa-solid ${it.icon}`} /> {it.label}
          </NavLink>
        ))}
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
