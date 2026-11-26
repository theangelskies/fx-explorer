import type { PairDetail } from "../api/queries";
import { daysBetween, formatDate, spanText } from "../lib/format";
import { WINDOW_LABEL } from "./WindowToggle";

/** The honesty note (ADR 0005): shown wherever trends appear, not only in the footer. */
export function Disclaimer() {
  return (
    <p className="flex items-start gap-2 rounded-lg bg-notice-bg px-3 py-2 text-sm text-notice-fg">
      <span aria-hidden="true">ⓘ</span>
      <span>Reference rates. Past trends don't predict future rates.</span>
    </p>
  );
}

/**
 * Partial history (docs/ui-design.md §5): the window asked for more than is stored.
 * The contract signals it with trend.period_start later than window_start.
 */
export function PartialHistoryNote({ pair }: { pair: PairDetail }) {
  const start = pair.trend?.period_start;
  if (!start || start <= pair.window_start) return null; // ISO dates compare correctly as text
  const span = spanText(daysBetween(start, pair.trend!.period_end));
  return (
    <p role="note" className="rounded-lg bg-notice-bg px-3 py-2 text-sm text-notice-fg">
      Only {span} of data for {pair.base}/{pair.quote} (since {formatDate(start)}); you asked for{" "}
      {WINDOW_LABEL[pair.window]}.
    </p>
  );
}
