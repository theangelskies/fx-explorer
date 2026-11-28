import { direction, formatDecimal, formatPct } from "../lib/format";

const SYMBOL = { up: "▲", down: "▼", flat: "▬" } as const;
const COLOUR = { up: "text-up", down: "text-down", flat: "text-muted" } as const;
const WORD = { up: "up", down: "down", flat: "unchanged" } as const;

interface ChangeProps {
  pct: string; // signed decimal string, e.g. "-0.2628"
  abs?: string | null; // optional absolute change in rate units
  decimals?: number; // round the % for display (cards: 2); omit for the exact value
  className?: string;
}

/**
 * An up/down move (docs/ui-design.md §2.1): symbol + sign + colour, so it never relies on colour
 * alone; screen readers hear "down 0.26 percent".
 */
export function Change({ pct, abs, decimals, className = "" }: ChangeProps) {
  const dir = direction(pct);
  return (
    <span className={`inline-flex items-baseline gap-1 font-medium tabular-nums ${COLOUR[dir]} ${className}`}>
      <span aria-hidden="true">{SYMBOL[dir]}</span>
      <span className="sr-only">{WORD[dir]}</span>
      {abs != null && <span>{(direction(abs) === "up" ? "+" : "") + formatDecimal(abs)}</span>}
      <span>{abs != null ? `(${formatPct(pct, decimals)})` : formatPct(pct, decimals)}</span>
    </span>
  );
}
