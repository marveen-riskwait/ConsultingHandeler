// Permission helper for the frontend. The backend is the real gate; this only
// decides what to *show*. `user.permissions` is populated from /auth/me.
export const can = (user, code) => Boolean(user?.permissions?.includes(code));

// Given a list of {code} items, keep the ones the user is allowed to see.
export const filterByPermission = (items, user) =>
  items.filter((it) => !it.permission || can(user, it.permission));
