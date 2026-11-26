import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  hint?: ReactNode; // what to do next, e.g. "Try a wider date range"
  action?: ReactNode; // optional button/link
}

/** Empty state (docs/ui-design.md §5): a friendly message plus the next step. */
export function EmptyState({ title, hint, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-line px-6 py-10 text-center">
      <p className="font-medium">{title}</p>
      {hint && <p className="max-w-prose text-sm text-muted">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
