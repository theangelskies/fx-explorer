/**
 * Trend panel (docs/ui-design.md §4.3). Shows every PairTrend field exactly as computed by the API
 * (ADR 0005). A null value is shown as "—" with the reason, never as 0.
 */
import type { ReactNode } from "react";

import type { PairDetail } from "../api/queries";
import { formatDate, formatDecimal } from "../lib/format";
import { Change } from "./Change";
import { WINDOW_LABEL } from "./WindowToggle";

function Missing({ reason }: { reason: string }) {
  return (
    <span className="text-muted" title={reason}>
      — <span className="text-xs">({reason})</span>
    </span>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 border-b border-line/60 py-2 last:border-0">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="text-right text-sm tabular-nums">{children}</dd>
    </div>
  );
}

export function TrendPanel({ pair }: { pair: PairDetail }) {
  const t = pair.trend;
  if (!t) return <p className="text-sm text-muted">No rates in this window.</p>;

  const maComparison =
    t.moving_average_30 && t.moving_average_200
      ? Number(t.moving_average_30) < Number(t.moving_average_200) ? "below" : Number(t.moving_average_30) > Number(t.moving_average_200) ? "above" : "equal to"
      : null;

  return (
    <div>
      <dl>
        <Row label={`Change (${WINDOW_LABEL[pair.window]})`}>
          <Change pct={t.change_pct} abs={t.change_abs} />
        </Row>
        <Row label="Per year">
          {t.annualized_change_pct ? <Change pct={t.annualized_change_pct} /> : <Missing reason="needs at least 1 year of data" />}
        </Row>
        <Row label="Volatility">
          {t.volatility_annualized_pct ? (
            <span title="How much the rate typically moves in a year. Higher = bumpier.">
              {formatDecimal(t.volatility_annualized_pct)}% a year
            </span>
          ) : (
            <Missing reason="needs at least 20 daily rates" />
          )}
        </Row>
        <Row label="Biggest rise">
          {t.biggest_rise ? (
            <>
              <Change pct={t.biggest_rise.change_pct} /> <span className="text-muted">{formatDate(t.biggest_rise.date)}</span>
            </>
          ) : (
            <Missing reason="no day rose in this period" />
          )}
        </Row>
        <Row label="Biggest fall">
          {t.biggest_fall ? (
            <>
              <Change pct={t.biggest_fall.change_pct} /> <span className="text-muted">{formatDate(t.biggest_fall.date)}</span>
            </>
          ) : (
            <Missing reason="no day fell in this period" />
          )}
        </Row>
        <Row label="MA30 vs MA200 (last 30 / 200 daily rates)">
          {t.moving_average_30 ? formatDecimal(t.moving_average_30) : <Missing reason="needs 30 daily rates" />}
          {" vs "}
          {t.moving_average_200 ? formatDecimal(t.moving_average_200) : <Missing reason="needs 200 daily rates" />}
          {maComparison && <span className="text-muted"> (30-day average is {maComparison} the 200-day)</span>}
        </Row>
        {pair.stats && (
          <Row label="Low · average · high">
            {formatDecimal(pair.stats.min)} · {formatDecimal(pair.stats.avg)} · {formatDecimal(pair.stats.max)}
          </Row>
        )}
      </dl>
      <p className="mt-3 text-xs text-muted">
        {formatDate(t.period_start)} – {formatDate(t.period_end)} · based on{" "}
        {pair.data_points.toLocaleString()} daily rates · data since {formatDate(pair.first_available_date)}.
      </p>
    </div>
  );
}
