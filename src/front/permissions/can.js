// Permission helper for the frontend. The backend is the real gate; this only
// decides what to *show*. `user.permissions` is populated from /auth/me.
export const can = (user, code) => Boolean(user?.permissions?.includes(code));

// Any-of: some screens serve two audiences. The watchlists are the example —
// an administrator maintains the lists (regulatory.*) while an analyst screens
// against them (screening.*), and neither role holds the other's permission.
export const canAny = (user, codes) =>
  (Array.isArray(codes) ? codes : [codes]).some((c) => can(user, c));

// Given a list of {code} items, keep the ones the user is allowed to see.
export const filterByPermission = (items, user) =>
  items.filter((it) => !it.permission || canAny(user, it.permission));
