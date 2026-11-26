interface PaginationProps {
  page: number; // 1-based, for display only (cursor pagination has no total, ADR 0003)
  hasPrevious: boolean;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  busy?: boolean;
}

const button =
  "rounded-md border border-line-input px-3 py-1.5 text-sm font-medium hover:bg-line/40 " +
  "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent";

/** Previous / Page N / Next. No "page 3 of 20": the API has no total count, on purpose. */
export function Pagination({ page, hasPrevious, hasNext, onPrevious, onNext, busy }: PaginationProps) {
  return (
    <nav aria-label="Pagination" className="flex items-center gap-3">
      <button type="button" className={button} onClick={onPrevious} disabled={!hasPrevious || busy}>
        <span aria-hidden="true">‹ </span>Previous
      </button>
      <span className="text-sm text-muted tabular-nums" aria-live="polite">
        Page {page}
      </span>
      <button type="button" className={button} onClick={onNext} disabled={!hasNext || busy}>
        Next<span aria-hidden="true"> ›</span>
      </button>
    </nav>
  );
}
