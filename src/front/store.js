// Global store holds the authenticated session. The JWT itself lives in
// localStorage (read by the API service); the store mirrors user + org so the
// UI can react to login/logout.
export const initialStore = () => {
  let user = null;
  try {
    const raw = localStorage.getItem("user");
    user = raw ? JSON.parse(raw) : null;
  } catch (e) {
    user = null;
  }
  return {
    token: localStorage.getItem("token") || null,
    user,
    organization: null,
  };
};

export default function storeReducer(store, action = {}) {
  switch (action.type) {
    case "login": {
      const { token, user } = action.payload;
      localStorage.setItem("token", token);
      localStorage.setItem("user", JSON.stringify(user));
      return { ...store, token, user };
    }
    case "set_me":
      return { ...store, user: action.payload.user, organization: action.payload.organization };
    case "logout":
      localStorage.removeItem("token");
      localStorage.removeItem("user");
      return { ...store, token: null, user: null, organization: null };
    default:
      return store;
  }
}
