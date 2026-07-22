// The JWT now lives in an httpOnly cookie the browser manages — JavaScript
// cannot read it, which is the whole point (an XSS cannot steal it). The store
// keeps only a cached `user` so the UI does not flash empty on reload, and an
// `authed` marker (not a secret) so the router knows a session exists before
// /auth/me confirms it. `token` is kept as a truthy sentinel purely so the
// many `store.token` checks keep working.
export const initialStore = () => {
  localStorage.removeItem("token");   // a JWT must never sit in JS storage
  let user = null;
  try {
    const raw = localStorage.getItem("user");
    user = raw ? JSON.parse(raw) : null;
  } catch (e) {
    user = null;
  }
  const authed = localStorage.getItem("authed") === "1";
  return { token: authed ? "cookie" : null, user, organization: null };
};

export default function storeReducer(store, action = {}) {
  switch (action.type) {
    case "login": {
      const { user } = action.payload;   // the token is a cookie, not in the body
      localStorage.removeItem("token");   // scrub any pre-migration JWT
      localStorage.setItem("authed", "1");
      localStorage.setItem("user", JSON.stringify(user));
      return { ...store, token: "cookie", user };
    }
    case "set_me":
      localStorage.setItem("authed", "1");
      localStorage.setItem("user", JSON.stringify(action.payload.user));
      return { ...store, token: "cookie", user: action.payload.user,
               organization: action.payload.organization };
    case "logout":
      localStorage.removeItem("token");   // scrub any pre-migration JWT
      localStorage.removeItem("authed");
      localStorage.removeItem("user");
      return { ...store, token: null, user: null, organization: null };
    default:
      return store;
  }
}
