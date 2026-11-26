import { useRouteError } from "react-router";

import { describeError } from "../api/errors";
import { ErrorState } from "../components/ErrorState";

/** Last-resort screen if a page crashes while rendering. */
export function RouteError() {
  const error = useRouteError();
  return (
    <div className="mx-auto max-w-xl p-6">
      <ErrorState problem={describeError(error)} onRetry={() => window.location.reload()} />
    </div>
  );
}
