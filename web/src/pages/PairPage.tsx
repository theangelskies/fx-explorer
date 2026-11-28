/**
 * Pair detail (docs/ui-design.md §4.3): long-range chart, trend panel, recent rates.
 * Operations: getPair (window, interval), getCurrency ×2, listRates (recent), listCurrencies (404 help).
 */
import { Link, useParams, useSearchParams } from "react-router";

import { ApiError, describeError } from "../api/errors";
import {
  useCurrencies,
  useCurrency,
  usePair,
  useRates,
  type PairDetail,
  type SeriesInterval,
  type Window,
} from "../api/queries";
import { Card } from "../components/Card";
import { Change } from "../components/Change";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Loading, Skeleton } from "../components/Loading";
import { Disclaimer, PartialHistoryNote } from "../components/Notices";
import { PairChart } from "../components/PairChart";
import { TrendPanel } from "../components/TrendPanel";
import { WINDOW_LABEL, WindowToggle } from "../components/WindowToggle";
import { formatDate, formatDecimal, formatPct } from "../lib/format";

const INTERVALS: SeriesInterval[] = ["day", "week", "month"];

export function PairPage() {
  const { base = "", quote = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  // The URL value is sent as is: an invalid ?window=2w comes back as the API's 400 message.
  const window = (searchParams.get("window") ?? "30d") as Window;
  const intervalParam = searchParams.get("interval");
  const interval = (intervalParam ?? undefined) as SeriesInterval | undefined;

  const pair = usePair(base, quote, window, interval);
  const baseInfo = useCurrency(base);
  const quoteInfo = useCurrency(quote);

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setSearchParams(next, { replace: true });
  };

  const names =
    baseInfo.data && quoteInfo.data ? `${baseInfo.data.name} → ${quoteInfo.data.name}` : null;

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">
          {base} / {quote}
        </h1>
        {names && <p className="text-sm text-muted">{names}</p>}
      </div>

      {pair.isPending ? (
        <PairSkeleton />
      ) : pair.isError ? (
        pair.error instanceof ApiError && pair.error.status === 404 ? (
          <NoSuchPair base={base} quote={quote} />
        ) : (
          <ErrorState problem={describeError(pair.error)} onRetry={() => pair.refetch()} />
        )
      ) : (
        <PairContent
          pair={pair.data}
          busy={pair.isPlaceholderData}
          intervalChoice={interval ?? null}
          onWindow={(w) => setParam("window", w === "30d" ? null : w)}
          onInterval={(i) => setParam("interval", i)}
        />
      )}
    </>
  );
}

interface PairContentProps {
  pair: PairDetail;
  busy: boolean; // showing the previous window while the new one loads
  intervalChoice: SeriesInterval | null;
  onWindow: (window: Window) => void;
  onInterval: (interval: SeriesInterval | null) => void;
}

function PairContent({ pair, busy, intervalChoice, onWindow, onInterval }: PairContentProps) {
  const t = pair.trend;
  const chartLabel = `${pair.base}/${pair.quote}, ${WINDOW_LABEL[pair.window]}` +
    (t ? `, ${formatPct(t.change_pct, 2)}` : "");
  const movingAverages = [
    t?.moving_average_30 && { label: "MA30", value: t.moving_average_30 },
    t?.moving_average_200 && { label: "MA200", value: t.moving_average_200 },
  ].filter(Boolean) as { label: string; value: string }[];

  return (
    <div className="space-y-4">
      {/* Latest rate and the day-over-day change (top-level change_*), labelled with its date. */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-3xl font-semibold tabular-nums">{formatDecimal(pair.latest.rate)}</p>
          <p className="text-sm text-muted">
            {formatDate(pair.latest.rate_date)}
            {pair.change_pct && pair.previous && (
              <>
                {" · "}
                <Change pct={pair.change_pct} abs={pair.change_abs} /> vs {formatDate(pair.previous.rate_date)}
              </>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <WindowToggle value={pair.window} onChange={onWindow} />
          <label className="flex items-center gap-1.5 text-xs text-muted">
            Chart points
            <select
              className="rounded-md border border-line-input bg-surface px-1.5 py-1 text-xs text-fg"
              value={intervalChoice ?? ""}
              onChange={(e) => onInterval((e.target.value || null) as SeriesInterval | null)}
            >
              <option value="">Auto ({pair.series_interval})</option>
              {INTERVALS.map((i) => (
                <option key={i} value={i}>{i}</option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <PartialHistoryNote pair={pair} />

      <Card className={`transition-opacity ${busy ? "opacity-60" : ""}`}>
        <div aria-busy={busy}>
          <PairChart
            series={pair.series}
            window={pair.window}
            interval={pair.series_interval}
            movingAverages={movingAverages}
            label={chartLabel}
          />
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Card title={`Trend · ${WINDOW_LABEL[pair.window]}`} className={busy ? "opacity-60" : ""}>
          <TrendPanel pair={pair} />
        </Card>
        <RecentRates base={pair.base} quote={pair.quote} />
      </div>

      <Disclaimer />
    </div>
  );
}

function RecentRates({ base, quote }: { base: string; quote: string }) {
  const rates = useRates({ base, quotes: [quote], limit: 10, sort: "date_desc" });
  const explorer = `/rates?${new URLSearchParams({ ...(base !== "EUR" && { base }), quotes: quote })}`;
  return (
    <Card
      title="Recent rates"
      action={<Link to={explorer} className="text-sm text-accent hover:underline">View all →</Link>}
    >
      {rates.isPending ? (
        <Loading label="Loading recent rates…" rows={6} />
      ) : rates.isError ? (
        <ErrorState problem={describeError(rates.error)} onRetry={() => rates.refetch()} />
      ) : rates.data.data.length === 0 ? (
        <EmptyState title="No recent rates" />
      ) : (
        <table className="w-full text-sm">
          <caption className="sr-only">Last 10 stored rates for {base}/{quote}</caption>
          <tbody>
            {rates.data.data.map((r) => (
              <tr key={r.rate_date} className="border-b border-line/60 last:border-0">
                <td className="py-1.5 text-muted tabular-nums">{formatDate(r.rate_date)}</td>
                <td className="py-1.5 text-right font-medium tabular-nums">{formatDecimal(r.rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

/** 404 = this pair has no stored rates: an empty state with pairs that do exist. */
function NoSuchPair({ base, quote }: { base: string; quote: string }) {
  const currencies = useCurrencies({ hasRates: true });
  const pairs = (currencies.data ?? []).filter((c) => c.iso_code !== "EUR").map((c) => c.iso_code);
  return (
    <EmptyState
      title={`No rates stored for ${base}/${quote}`}
      hint="Only pairs the importer stores are available (with EUR as the base); cross rates aren't calculated."
      action={
        pairs.length > 0 && (
          <div className="flex flex-wrap justify-center gap-2">
            {pairs.map((q) => (
              <Link key={q} to={`/pairs/EUR/${q}`} className="rounded-md border border-line-input px-2.5 py-1 text-sm hover:bg-line/40">
                EUR/{q}
              </Link>
            ))}
          </div>
        )
      }
    />
  );
}

function PairSkeleton() {
  return (
    <div className="space-y-4" role="status" aria-busy="true">
      <span className="sr-only">Loading pair…</span>
      <Skeleton className="h-10 w-48" />
      <Skeleton className="h-72 w-full" />
      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Skeleton className="h-64" />
        <Skeleton className="h-64" />
      </div>
    </div>
  );
}
