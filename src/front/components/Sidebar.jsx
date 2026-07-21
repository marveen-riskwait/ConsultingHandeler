import { useCallback, useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import useGlobalReducer from "../hooks/useGlobalReducer";
import { filterByPermission } from "../permissions/can";
import { api } from "../services/api";
import { getSocket } from "../services/socket";

// Vertical navigation is generated from the user's permissions. The backend
// still enforces access — this only hides what the user cannot use.
const NAV_ITEMS = [
  { to: "/", end: true, icon: "fa-inbox", label: "My Work", permission: "workspace.view" },
  { to: "/assistant", icon: "fa-robot", label: "Copilot", permission: "workspace.view" },
  { to: "/customers", icon: "fa-users", label: "Customers", permission: "customer.view" },
  { to: "/alerts", icon: "fa-triangle-exclamation", label: "Alerts", permission: "case.view" },
  { to: "/management", icon: "fa-chart-line", label: "Management", permission: "management.view" },
  { to: "/regulatory", icon: "fa-scale-balanced", label: "Regulatory", permission: "regulatory.view" },
  { to: "/audit", icon: "fa-clipboard-list", label: "Audit", permission: "audit.view" },
  { to: "/administration", icon: "fa-gear", label: "Admin", permission: "user.view" },
];

export const Sidebar = () => {
  const { store } = useGlobalReducer();
  const location = useLocation();
  const items = filterByPermission(NAV_ITEMS, store.user);
  const role = store.user?.role || "";
  const [unread, setUnread] = useState(0);

  // Total unread messages: refreshed on navigation and pushed live over the
  // socket whenever a chat message arrives anywhere in the org.
  const refreshUnread = useCallback(() => {
    api.chatRooms()
      .then((rooms) => setUnread(rooms.reduce((n, r) => n + (r.unread || 0), 0)))
      .catch(() => {});
  }, []);

  useEffect(() => { refreshUnread(); }, [refreshUnread, location.pathname]);

  useEffect(() => {
    const s = getSocket();
    if (!s) return undefined;
    const onEvent = () => refreshUnread();
    s.on("chat:message", onEvent);
    s.on("chat:room-created", onEvent);
    return () => { s.off("chat:message", onEvent); s.off("chat:room-created", onEvent); };
  }, [refreshUnread]);

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
        <NavLink to="/chat"
          className={({ isActive }) => "co-sidebar-link" + (isActive ? " active" : "")}>
          <i className="fa-solid fa-comments" />
          <span>Team Chat</span>
          {unread > 0 && <span className="ch-nav-badge">{unread}</span>}
        </NavLink>
        <span className="co-role-badge">{role.replace(/_/g, " ")}</span>
      </div>
    </aside>
  );
};
