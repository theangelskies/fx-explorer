import { useEffect, useState } from "react";

/** A grey placeholder block shaped like the content that's coming. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden="true" className={`skeleton rounded-md ${className}`} />;
}

interface LoadingProps {
  label?: string; // read by screen readers
  rows?: number; // how many placeholder lines
}

/**
 * Loading state (docs/ui-design.md §5): skeleton rows, shown only after 150 ms so that fast
 * answers don't flash a placeholder.
 */
export function Loading({ label = "Loading…", rows = 3 }: LoadingProps) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setVisible(true), 150);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <div role="status" aria-busy="true" aria-live="polite" className="space-y-3">
      <span className="sr-only">{label}</span>
      {visible &&
        Array.from({ length: rows }, (_, i) => (
          <Skeleton key={i} className={`h-5 ${i % 3 === 2 ? "w-2/3" : "w-full"}`} />
        ))}
    </div>
  );
}
