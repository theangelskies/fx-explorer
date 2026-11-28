/**
 * Compare two pairs on one chart. Their rates live on different scales (EUR/USD ≈ 1.1,
 * EUR/JPY ≈ 175), so both are shown as % change since the first point of the chart.
 *
 * The two lines differ in colour AND pattern (solid vs dashed) and are named in the legend and
 * tooltip, so they can be told apart without colour. Number() is for drawing only (ADR 0004);
 * the tooltip also shows each pair's exact rate string.
 */
import {
  Brush,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { SeriesInterval, Window } from "../api/queries";
import { formatDecimal } from "../lib/format";
import { BRUSH_MIN_POINTS, brushProps, tickFormatter } from "./PairChart";

interface Series {
  label: string; // e.g. "EUR/USD"
  points: { date: string; rate: string }[];
}

interface Row {
  date: string;
  a?: number;
  aRate?: string;
  b?: number;
  bRate?: string;
}

/** Merge both series by date (dates can differ, e.g. a currency a day behind). */
function merge(a: Series, b: Series): Row[] {
  const rows = new Map<string, Row>();
  const add = (s: Series, key: "a" | "b") => {
    const first = Number(s.points[0]?.rate ?? 1);
    for (const p of s.points) {
      const row = rows.get(p.date) ?? { date: p.date };
      row[key] = (Number(p.rate) / first - 1) * 100;
      row[key === "a" ? "aRate" : "bRate"] = p.rate;
      rows.set(p.date, row);
    }
  };
  add(a, "a");
  add(b, "b");
  return [...rows.values()].sort((x, y) => (x.date < y.date ? -1 : 1));
}

const tooltipDate = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const pct = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}%`;

function LegendLine({ dashed, colour, label }: { dashed?: boolean; colour: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg width="22" height="8" aria-hidden="true">
        <line x1="0" y1="4" x2="22" y2="4" stroke={colour} strokeWidth="2.5" strokeDasharray={dashed ? "5 3" : undefined} />
      </svg>
      {label}
    </span>
  );
}

interface CompareChartProps {
  primary: Series;
  secondary: Series;
  window: Window;
  interval: SeriesInterval;
}

export function CompareChart({ primary, secondary, window, interval }: CompareChartProps) {
  const data = merge(primary, secondary);
  const last = (key: "a" | "b") => [...data].reverse().find((r) => r[key] !== undefined)?.[key] ?? 0;
  const summary = `${primary.label} ${pct(last("a"))} vs ${secondary.label} ${pct(last("b"))} over the chart`;

  return (
    <figure className="m-0">
      <figcaption className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <LegendLine colour="var(--accent)" label={`${primary.label} ${pct(last("a"))}`} />
        <LegendLine dashed colour="var(--compare)" label={`${secondary.label} ${pct(last("b"))}`} />
        <span className="text-xs text-muted">% change since the start of the chart</span>
      </figcaption>
      <div className={`w-full ${data.length > BRUSH_MIN_POINTS ? "h-72 sm:h-96" : "h-64 sm:h-80"}`} role="img" aria-label={summary}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--line)" strokeDasharray="3 3" />
            <XAxis dataKey="date" tickFormatter={tickFormatter(window)} tick={{ fill: "var(--muted)", fontSize: 12 }}
              tickLine={false} axisLine={{ stroke: "var(--line)" }} minTickGap={32} />
            <YAxis tickFormatter={(v: number) => `${v.toFixed(1)}%`} tick={{ fill: "var(--muted)", fontSize: 12 }}
              tickLine={false} axisLine={false} width={56} />
            <ReferenceLine y={0} stroke="var(--muted)" />
            <Tooltip
              cursor={{ stroke: "var(--muted)", strokeDasharray: "3 3" }}
              content={({ active, payload }) => {
                const row = payload?.[0]?.payload as Row | undefined;
                if (!active || !row) return null;
                return (
                  <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-sm">
                    <p className="text-xs text-muted">{tooltipDate.format(new Date(`${row.date}T00:00:00Z`))}</p>
                    {row.a !== undefined && <p className="tabular-nums"><LegendLine colour="var(--accent)" label={`${primary.label} ${pct(row.a)} (${formatDecimal(row.aRate!)})`} /></p>}
                    {row.b !== undefined && <p className="tabular-nums"><LegendLine dashed colour="var(--compare)" label={`${secondary.label} ${pct(row.b)} (${formatDecimal(row.bRate!)})`} /></p>}
                  </div>
                );
              }}
            />
            <Line type="monotone" dataKey="a" name={primary.label} stroke="var(--accent)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
            <Line type="monotone" dataKey="b" name={secondary.label} stroke="var(--compare)" strokeWidth={2} strokeDasharray="6 4" dot={false} connectNulls isAnimationActive={false} />
            {data.length > BRUSH_MIN_POINTS && <Brush {...brushProps(window)} />}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 text-right text-xs text-muted">
        {{ day: "daily", week: "weekly", month: "monthly" }[interval]} points
        {data.length > BRUSH_MIN_POINTS && " · drag the bar below the chart to zoom"}
      </p>
    </figure>
  );
}
