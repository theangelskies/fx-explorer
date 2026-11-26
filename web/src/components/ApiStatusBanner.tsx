import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { call, describeError } from "../api/errors";

/**
 * "API offline" banner (docs/ui-design.md §7: getHealth). Checks every 60 s and shows nothing
 * while all is well; pages still show their own errors.
 */
export function ApiStatusBanner() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => call(api.GET("/v1/health")),
    refetchInterval: 60_000,
    retry: false,
  });

  if (!health.isError) return null;
  const problem = describeError(health.error);
  return (
    <div role="status" className="border-b border-line bg-notice-bg text-notice-fg">
      <div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm md:px-6">
        <strong>{problem.title}.</strong>
        <span>{problem.detail}</span>
        <button
          type="button"
          onClick={() => health.refetch()}
          className="underline underline-offset-2 hover:no-underline"
        >
          Check again
        </button>
      </div>
    </div>
  );
}
