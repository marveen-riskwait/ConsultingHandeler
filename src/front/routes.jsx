import {
  createBrowserRouter,
  createRoutesFromElements,
  Route,
  Navigate,
} from "react-router-dom";
import { Layout } from "./pages/Layout";
import { Login } from "./pages/Login";
import { Workspace } from "./pages/Workspace";
import { Customers } from "./pages/Customers";
import { Customer360 } from "./pages/Customer360";
import { CaseDetail } from "./pages/CaseDetail";
import { Administration } from "./pages/Administration";
import { Management } from "./pages/Management";
import { Alerts } from "./pages/Alerts";
import { Regulatory } from "./pages/Regulatory";
import { Audit } from "./pages/Audit";
import { Assistant } from "./pages/Assistant";

export const router = createBrowserRouter(
  createRoutesFromElements(
    <Route path="/" element={<Layout />} errorElement={<h1 className="co-container">Not found!</h1>}>
      <Route index element={<Workspace />} />
      {/* Logged-out: Layout renders the Login screen for this path. Logged-in
          visitors landing on /login are bounced to their workspace. */}
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route path="/assistant" element={<Assistant />} />
      <Route path="/customers" element={<Customers />} />
      <Route path="/customers/:id" element={<Customer360 />} />
      <Route path="/alerts" element={<Alerts />} />
      <Route path="/regulatory" element={<Regulatory />} />
      <Route path="/audit" element={<Audit />} />
      <Route path="/cases/:id" element={<CaseDetail />} />
      <Route path="/administration" element={<Administration />} />
      <Route path="/management" element={<Management />} />
    </Route>
  ),
  // Login sits outside the Layout (no navbar).
  { basename: import.meta.env.VITE_BASENAME || "/" }
);

// Note: the login route is handled inside Layout when there is no token.
export { Login };
