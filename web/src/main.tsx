import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import { ApiError } from "./api/errors";
import "./index.css";
import { router } from "./router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000, // rates change once a day: no need to refetch on every visit
      refetchOnWindowFocus: false,
      // Retry once only when the API couldn't be reached (status 0). A 4xx won't fix itself,
      // and the API already reported its own 5xx as a Problem.
      retry: (failureCount, error) =>
        error instanceof ApiError && error.status === 0 && failureCount < 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
