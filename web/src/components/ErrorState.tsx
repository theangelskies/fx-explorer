import type { DisplayProblem } from "../api/errors";

interface ErrorStateProps {
  problem: DisplayProblem;
  onRetry?: () => void;
}

/**
 * Error state (docs/ui-design.md §5): the Problem's title and detail, its field errors if any,
 * and a Retry button. Never raw JSON.
 */
export function ErrorState({ problem, onRetry }: ErrorStateProps) {
  return (
    <div role="alert" className="rounded-lg border border-down/40 bg-down/5 px-5 py-4">
      <p className="font-semibold text-down">{problem.title}</p>
      <p className="mt-1 text-sm">{problem.detail}</p>
      {problem.errors && problem.errors.length > 0 && (
        <ul className="mt-2 list-inside list-disc text-sm">
          {problem.errors.map((e) => (
            <li key={e.field}>
              <code className="font-mono">{e.field}</code>: {e.message}
            </li>
          ))}
        </ul>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-on-accent hover:opacity-90"
        >
          Retry
        </button>
      )}
    </div>
  );
}
