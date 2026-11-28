import { createBrowserRouter } from "react-router";

import { AppShell } from "./components/AppShell";
import { NotFoundPage } from "./pages/NotFoundPage";
import { RouteError } from "./pages/RouteError";

// Routes from docs/ui-design.md §3. Page state (filters, window …) lives in the URL query.
// Each page is loaded only when first visited (`lazy`), so e.g. the chart library isn't
// downloaded until a page with a chart is opened.
export const router = createBrowserRouter([
  {
    element: <AppShell />,
    errorElement: <RouteError />,
    children: [
      { index: true, lazy: () => import("./pages/DashboardPage").then((m) => ({ Component: m.DashboardPage })) },
      { path: "rates", lazy: () => import("./pages/RatesPage").then((m) => ({ Component: m.RatesPage })) },
      { path: "pairs/:base/:quote", lazy: () => import("./pages/PairPage").then((m) => ({ Component: m.PairPage })) },
      { path: "ask", lazy: () => import("./pages/AskPage").then((m) => ({ Component: m.AskPage })) },
      { path: "alerts", lazy: () => import("./pages/AlertsPage").then((m) => ({ Component: m.AlertsPage })) },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
