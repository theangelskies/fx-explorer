import type { Direction } from "../lib/format";

const COLOUR = { up: "text-up", down: "text-down", flat: "text-muted" } as const;

/**
 * A tiny line (docs/ui-design.md §2.4): no axes, no tooltip, coloured by direction. Decorative
 * (aria-hidden): the card always prints the change as text with ▲▼ next to it.
 * Plain SVG instead of the chart library, so KPI cards stay light. Number() is for drawing only.
 */
export function Sparkline({ values, direction }: { values: string[]; direction: Direction }) {
  const nums = values.map(Number);
  if (nums.length < 2) return <div className="h-8" aria-hidden="true" />;
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min || 1;
  const points = nums
    .map((v, i) => `${((i / (nums.length - 1)) * 100).toFixed(2)},${(30 - ((v - min) / span) * 28).toFixed(2)}`)
    .join(" ");
  return (
    <svg viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden="true" className={`h-8 w-full ${COLOUR[direction]}`}>
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
