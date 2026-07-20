import { NavLink } from "react-router-dom";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { filterByPermission } from "../permissions/can";

// Vertical navigation is generated from the user's permissions. The backend
// still enforces access — this only hides what the user cannot use.
const NAV_ITEMS = [
  { to: "/", end: true, icon: "fa-inbox", label: "My Work", permission: "workspace.view" },
  { to: "/customers", icon: "fa-users", label: "Customers", permission: "customer.view" },
  { to: "/alerts", icon: "fa-triangle-exclamation", label: "Alerts", permission: "case.view" },
  { to: "/management", icon: "fa-chart-line", label: "Management", permission: "management.view" },
  { to: "/regulatory", icon: "fa-scale-balanced", label: "Regulatory", permission: "regulatory.view" },
  { to: "/audit", icon: "fa-clipboard-list", label: "Audit", permission: "audit.view" },
  { to: "/administration", icon: "fa-gear", label: "Admin", permission: "user.view" },
];

export const Sidebar = () => {
  const { store } = useGlobalReducer();
  const items = filterByPermission(NAV_ITEMS, store.user);
  const role = store.user?.role || "";

  return (
    <aside className="co-sidebar">
      <div className="co-sidebar-brand">
        <span className="dot" /> Compliance OS
      </div>

      <div className="co-sidebar-group">Workspace</div>
      <nav className="co-sidebar-nav">
        {items.map((it) => (
          <NavLink key={it.to} to={it.to} end={it.end}
            className={({ isActive }) => "co-sidebar-link" + (isActive ? " active" : "")}>
            <i className={`fa-solid ${it.icon}`} />
            <span>{it.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="co-sidebar-foot">
        <span className="co-role-badge">{role.replace(/_/g, " ")}</span>
      </div>
    </aside>
  );
};
