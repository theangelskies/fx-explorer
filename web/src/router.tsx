import { createBrowserRouter } from "react-router";

import { AppShell } from "./components/AppShell";
import { AlertsPage } from "./pages/AlertsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PairPage } from "./pages/PairPage";
import { RatesPage } from "./pages/RatesPage";
import { RouteError } from "./pages/RouteError";

// Routes from docs/ui-design.md §3. Page state (filters, window …) lives in the URL query.
export const router = createBrowserRouter([
  {
    element: <AppShell />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "rates", element: <RatesPage /> },
      { path: "pairs/:base/:quote", element: <PairPage /> },
      { path: "alerts", element: <AlertsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
