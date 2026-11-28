/**
 * The pair chart (docs/ui-design.md §2.4): accent line with a faint area, horizontal dashed grid,
 * y-axis fitted to the data, tooltip with the EXACT rate string, optional MA reference lines.
 * Recharts needs numbers, so rates are converted with Number() for drawing only (ADR 0004).
 */
import { useId } from "react";
import {
  Area,
  AreaChart,
  Brush,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { SeriesInterval, Window } from "../api/queries";
import { formatDecimal } from "../lib/format";

interface Point {
  date: string;
  rate: string; // exact, for display
  value: number; // for drawing
}

interface PairChartProps {
  series: { date: string; rate: string }[];
  window: Window;
  interval: SeriesInterval;
  movingAverages?: { label: string; value: string }[]; // drawn as dashed horizontal lines
  label: string; // accessible summary, e.g. "EUR/USD, 5 years, down 2.78%"
}

export function tickFormatter(window: Window): (date: string) => string {
  const opts: Intl.DateTimeFormatOptions =
    window === "max" ? { year: "numeric" }
    : window === "1y" || window === "3y" || window === "5y" ? { month: "short", year: "2-digit" }
    : { day: "numeric", month: "short" };
  const fmt = new Intl.DateTimeFormat(undefined, { ...opts, timeZone: "UTC" });
  return (date) => fmt.format(new Date(`${date}T00:00:00Z`));
}

const tooltipDate = new Intl.DateTimeFormat(undefined, {
  weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
});

interface ChartTooltipProps {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: unknown }>;
  first: number; // value of the first point, for "since start of chart"
}

function ChartTooltip({ active, payload, first }: ChartTooltipProps) {
  const point = payload?.[0]?.payload as Point | undefined;
  if (!active || !point) return null;
  const since = ((point.value / first - 1) * 100).toFixed(2); // display only
  const dir = Number(since) > 0 ? "up" : Number(since) < 0 ? "down" : "flat";
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-sm">
      <p className="text-xs text-muted">{tooltipDate.format(new Date(`${point.date}T00:00:00Z`))}</p>
      <p className="font-semibold tabular-nums">{formatDecimal(point.rate)}</p>
      <p className={`text-xs tabular-nums ${dir === "up" ? "text-up" : dir === "down" ? "text-down" : "text-muted"}`}>
        {dir === "up" ? "▲ +" : dir === "down" ? "▼ −" : "▬ "}
        {since.replace("-", "")}% since start of chart
      </p>
    </div>
  );
}

/** Points needed before the zoom bar appears (short charts don't need it). */
export const BRUSH_MIN_POINTS = 60;

/**
 * Props for the zoom bar under long charts: drag its handles (or the window between them) to zoom
 * into a period. Mouse/touch only; the window toggle is the keyboard way to change the range.
 * Used as <Brush {...brushProps(window)} /> placed DIRECTLY inside the chart (Recharts finds its
 * parts among the chart's children).
 */
export function brushProps(window: Window) {
  return {
    dataKey: "date",
    height: 22,
    travellerWidth: 10,
    stroke: "var(--accent)",
    fill: "var(--surface)",
    tickFormatter: tickFormatter(window),
  };
}

export function PairChart({ series, window, interval, movingAverages = [], label }: PairChartProps) {
  const gradientId = useId();
  const data: Point[] = series.map((p) => ({ date: p.date, rate: p.rate, value: Number(p.rate) }));
  const first = data[0]?.value ?? 1;
  const pointsLabel = { day: "daily", week: "weekly", month: "monthly" }[interval];

  return (
    <figure className="m-0">
      <div
        className={`w-full ${data.length > BRUSH_MIN_POINTS ? "h-72 sm:h-96" : "h-64 sm:h-80"}`}
        role="img"
        aria-label={`${label}. Chart of ${data.length} ${pointsLabel} points; details in the trend panel.`}
      >
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.18} />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="var(--line)" strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={tickFormatter(window)}
              tick={{ fill: "var(--muted)", fontSize: 12 }}
              tickLine={false}
              axisLine={{ stroke: "var(--line)" }}
              minTickGap={32}
            />
            <YAxis
              domain={["auto", "auto"]}
              tick={{ fill: "var(--muted)", fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              width={64}
              tickFormatter={(v: number) => v.toLocaleString(undefined, { maximumSignificantDigits: 5 })}
            />
            <Tooltip
              content={(props) => (
                <ChartTooltip active={props.active} payload={props.payload} first={first} />
              )}
              cursor={{ stroke: "var(--muted)", strokeDasharray: "3 3" }}
            />
            {movingAverages.map((ma) => (
              <ReferenceLine
                key={ma.label}
                y={Number(ma.value)}
                stroke="var(--muted)"
                strokeDasharray="6 4"
                label={{ value: ma.label, position: "insideTopRight", fill: "var(--muted)", fontSize: 11 }}
              />
            ))}
            <Area
              type="monotone"
              dataKey="value"
              stroke="var(--accent)"
              strokeWidth={2}
              fill={`url(#${gradientId})`}
              dot={false}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
            {data.length > BRUSH_MIN_POINTS && <Brush {...brushProps(window)} />}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-1 text-right text-xs text-muted">
        {pointsLabel} points{data.length > BRUSH_MIN_POINTS && " · drag the bar below the chart to zoom"}
      </figcaption>
    </figure>
  );
}
