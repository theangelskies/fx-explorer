/**
 * Dashboard cards (docs/ui-design.md §4.1). Each card loads its own data and shows its own
 * loading / empty / error state, so one failing card never blanks the whole page.
 */
import { useState } from "react";
import { Link } from "react-router";

import { ApiError, describeError } from "../../api/errors";
import { useLatestImportRun, useMovers, usePair, type Window } from "../../api/queries";
import { direction, formatDate, formatDateTime, formatDecimal, formatRelative } from "../../lib/format";
import { Card } from "../Card";
import { Change } from "../Change";
import { EmptyState } from "../EmptyState";
import { ErrorState } from "../ErrorState";
import { Loading, Skeleton } from "../Loading";
import { Disclaimer } from "../Notices";
import { PairChart } from "../PairChart";
import { Sparkline } from "../Sparkline";
import { WINDOW_LABEL, WindowToggle } from "../WindowToggle";

const BASE = "EUR"; // the only base the importer stores (docs/ui-design.md §4.1, gap G5)

/** A small inline error with Retry, for one card. */
function CardError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const p = describeError(error);
  return (
    <p role="alert" className="text-sm text-down">
      {p.title}.{" "}
      <button type="button" className="underline" onClick={onRetry}>Retry</button>
    </p>
  );
}

// ------------------------------------------------------------------ freshness

/** "Data: 26 Nov 2026 · imported 2 h ago ✓", or a running / failed / never notice. */
export function Freshness({ latestRateDate }: { latestRateDate: string | null }) {
  const run = useLatestImportRun();
  const dataPart = latestRateDate ? `Data up to ${formatDate(latestRateDate)}` : null;

  let runPart: React.ReactNode;
  if (run.isPending) runPart = <Skeleton className="inline-block h-4 w-32 align-middle" />;
  else if (run.isError) {
    runPart = run.error instanceof ApiError && run.error.status === 404
      ? <span className="text-notice-fg">No import has run yet</span>
      : <span className="text-muted">import status unavailable</span>;
  } else {
    const r = run.data;
    const when = r.finished_at ?? r.started_at;
    const ago = <time dateTime={when} title={formatDateTime(when)}>{formatRelative(when)}</time>;
    runPart =
      r.status === "running" ? <span>import running (started {ago})…</span>
      : r.status === "failed" ? (
        <span className="text-down" title={r.error_message ?? undefined}>
          <span aria-hidden="true">✕ </span>last import failed {ago}
        </span>
      )
      : r.status === "partial" ? <span className="text-notice-fg">imported {ago}, {r.rejected} records rejected</span>
      : <span>imported {ago} <span aria-hidden="true" className="text-up">✓</span></span>;
  }

  return (
    <p className="text-sm text-muted" aria-live="polite">
      {dataPart}
      {dataPart && " · "}
      {runPart}
    </p>
  );
}

// ------------------------------------------------------------------ KPI cards

export function KpiCard({ quote }: { quote: string }) {
  const pair = usePair(BASE, quote, "30d");
  return (
    <Link to={`/pairs/${BASE}/${quote}`}
      className="block rounded-xl border border-line bg-surface p-4 shadow-sm transition hover:border-accent dark:shadow-none">
      <p className="text-xs font-semibold text-muted">{BASE}/{quote}</p>
      {pair.isPending ? (
        <div className="mt-2 space-y-2"><Skeleton className="h-7 w-24" /><Skeleton className="h-4 w-20" /><Skeleton className="h-8 w-full" /></div>
      ) : pair.isError ? (
        <p className="mt-2 text-sm text-down">{describeError(pair.error).title}</p>
      ) : (
        <>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{formatDecimal(pair.data.latest.rate)}</p>
          <p className="text-xs text-muted">
            {pair.data.change_pct ? <Change pct={pair.data.change_pct} decimals={2} /> : "—"}{" "}
            <span title={pair.data.previous ? `vs ${formatDate(pair.data.previous.rate_date)}` : undefined}>
              on {formatDate(pair.data.latest.rate_date)}
            </span>
          </p>
          {pair.data.trend && (
            <div className="mt-2">
              <Sparkline values={pair.data.series.map((p) => p.rate)} direction={direction(pair.data.trend.change_pct)} />
              <p className="text-right text-[11px] text-muted">
                30 days <Change pct={pair.data.trend.change_pct} decimals={2} />
              </p>
            </div>
          )}
        </>
      )}
    </Link>
  );
}

// ------------------------------------------------------------------ main chart

export function MainChart({ quotes }: { quotes: string[] }) {
  const [quote, setQuote] = useState(quotes.includes("USD") ? "USD" : quotes[0]!);
  const [window, setWindow] = useState<Window>("1y");
  const pair = usePair(BASE, quote, window);

  return (
    <Card
      title={
        <label className="flex items-center gap-2">
          <span className="sr-only">Pair</span>
          <select value={quote} onChange={(e) => setQuote(e.target.value)}
            className="rounded-md border border-line-input bg-surface px-2 py-1 text-base font-semibold">
            {quotes.map((q) => <option key={q} value={q}>{BASE}/{q}</option>)}
          </select>
        </label>
      }
      action={<WindowToggle value={window} onChange={setWindow} />}
    >
      {pair.isPending ? (
        <Skeleton className="h-64 w-full sm:h-80" />
      ) : pair.isError ? (
        <ErrorState problem={describeError(pair.error)} onRetry={() => pair.refetch()} />
      ) : (
        <div className={pair.isPlaceholderData ? "opacity-60 transition-opacity" : ""} aria-busy={pair.isPlaceholderData}>
          <PairChart series={pair.data.series} window={pair.data.window} interval={pair.data.series_interval}
            label={`${BASE}/${quote}, ${WINDOW_LABEL[window]}`} />
          <p className="mt-2 text-right text-sm">
            <Link to={`/pairs/${BASE}/${quote}?window=${window}`} className="text-accent hover:underline">Trend details →</Link>
          </p>
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ movers

export function MoversCard() {
  const movers = useMovers(5);
  return (
    <Card title="Biggest movers" action={<span className="text-xs text-muted">latest day per currency</span>}>
      {movers.isPending ? (
        <Loading label="Loading movers…" rows={5} />
      ) : movers.isError ? (
        <CardError error={movers.error} onRetry={() => movers.refetch()} />
      ) : movers.data.data.length === 0 ? (
        <EmptyState title="No moves yet" hint="Each currency needs at least two stored rates." />
      ) : (
        <ol className="space-y-2">
          {movers.data.data.map((m) => (
            <li key={m.quote} className="flex items-baseline justify-between gap-3 text-sm">
              <Link to={`/pairs/${movers.data.base}/${m.quote}`} className="font-semibold hover:text-accent">{m.quote}</Link>
              <span className="flex-1 text-xs text-muted" title={`${formatDecimal(m.previous_rate)} → ${formatDecimal(m.rate)}`}>
                {formatDate(m.rate_date)}
              </span>
              <Change pct={m.change_pct} decimals={2} />
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ converter

/** Quick converter (EUR ↔ X only: gap G3, decided). Local maths, updates as you type. */
export function Converter({ quotes }: { quotes: string[] }) {
  const [quote, setQuote] = useState(quotes.includes("USD") ? "USD" : quotes[0]!);
  const [amount, setAmount] = useState("100");
  const [fromBase, setFromBase] = useState(true); // true: EUR -> quote, false: quote -> EUR
  const pair = usePair(BASE, quote, "30d"); // same query as the KPI card: usually already cached

  // Accept "1234.5" or "1234,5". Number() here is for a display-only result (ADR 0004).
  const normalised = amount.trim().replace(/\s/g, "").replace(/,(?=\d*$)/, ".");
  const value = normalised === "" ? NaN : Number(normalised);
  const rate = pair.data ? Number(pair.data.latest.rate) : NaN;
  const result = fromBase ? value * rate : value / rate;
  const from = fromBase ? BASE : quote;
  const to = fromBase ? quote : BASE;
  const fmt = (n: number) => n.toLocaleString(undefined, Math.abs(n) >= 1 ? { maximumFractionDigits: 2 } : { maximumSignificantDigits: 6 });

  return (
    <Card title="Quick converter">
      <div className="space-y-3">
        <div className="flex gap-2">
          <label className="sr-only" htmlFor="conv-amount">Amount in {from}</label>
          <input id="conv-amount" inputMode="decimal" autoComplete="off" value={amount} onChange={(e) => setAmount(e.target.value)}
            className="min-w-0 flex-1 rounded-md border border-line-input bg-surface px-2.5 py-1.5 text-right tabular-nums" />
          {fromBase ? <span className="self-center font-semibold">{BASE}</span> : (
            <select aria-label="Currency" value={quote} onChange={(e) => setQuote(e.target.value)}
              className="rounded-md border border-line-input bg-surface px-2 py-1.5 font-semibold">
              {quotes.map((q) => <option key={q}>{q}</option>)}
            </select>
          )}
        </div>
        <div className="flex justify-center">
          <button type="button" onClick={() => setFromBase((f) => !f)} aria-label={`Swap: convert ${to} to ${from}`}
            className="rounded-full border border-line-input px-3 py-0.5 text-sm hover:bg-line/40">⇅</button>
        </div>
        <div className="flex items-baseline justify-between gap-2" aria-live="polite">
          <p className="text-xl font-semibold tabular-nums">
            {pair.isPending ? "…" : pair.isError ? "—" : Number.isFinite(result) ? `= ${fmt(result)}` : "Enter a number"}
          </p>
          {fromBase ? (
            <select aria-label="Currency" value={quote} onChange={(e) => setQuote(e.target.value)}
              className="rounded-md border border-line-input bg-surface px-2 py-1.5 font-semibold">
              {quotes.map((q) => <option key={q}>{q}</option>)}
            </select>
          ) : <span className="font-semibold">{BASE}</span>}
        </div>
        <p className="text-xs text-muted">
          {pair.data
            ? <>1 {BASE} = {formatDecimal(pair.data.latest.rate)} {quote} · {formatDate(pair.data.latest.rate_date)}</>
            : pair.isError ? describeError(pair.error).title : "Loading rate…"}
        </p>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ long-term view

function LongTermRow({ quote }: { quote: string }) {
  const pair = usePair(BASE, quote, "5y");
  const label = <Link to={`/pairs/${BASE}/${quote}?window=5y`} className="font-semibold hover:text-accent">{BASE}/{quote}</Link>;
  if (pair.isPending) return <tr><th scope="row" className="py-2 pr-3 text-left">{label}</th><td colSpan={3}><Skeleton className="h-4 w-full" /></td></tr>;
  if (pair.isError || !pair.data.trend) {
    return <tr><th scope="row" className="py-2 pr-3 text-left">{label}</th><td colSpan={3} className="text-sm text-down">{pair.isError ? describeError(pair.error).title : "No data"}</td></tr>;
  }
  const t = pair.data.trend;
  const partial = t.period_start > pair.data.window_start; // less than 5 years stored
  return (
    <tr className="border-b border-line/60 last:border-0">
      <th scope="row" className="py-2 pr-3 text-left">
        {label}
        {partial && <span className="ml-1 text-xs font-normal text-notice-fg">since {t.period_start.slice(0, 4)}</span>}
      </th>
      <td className="py-2 pr-3 text-right"><Change pct={t.change_pct} decimals={2} /></td>
      <td className="py-2 pr-3 text-right">{t.annualized_change_pct ? <Change pct={t.annualized_change_pct} decimals={2} /> : <span className="text-muted">—</span>}</td>
      <td className="py-2 text-right tabular-nums">{t.volatility_annualized_pct ? `${Number(t.volatility_annualized_pct).toFixed(1)}%` : "—"}</td>
    </tr>
  );
}

export function LongTermCard({ quotes }: { quotes: string[] }) {
  return (
    <Card title="Long-term view · 5 years">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">Change over 5 years per pair</caption>
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th scope="col" className="py-2 pr-3 text-left font-medium">Pair</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">5-year change</th>
              <th scope="col" className="py-2 pr-3 text-right font-medium">Per year</th>
              <th scope="col" className="py-2 text-right font-medium" title="How much the rate typically moves in a year">Volatility</th>
            </tr>
          </thead>
          <tbody>{quotes.map((q) => <LongTermRow key={q} quote={q} />)}</tbody>
        </table>
      </div>
      <div className="mt-4"><Disclaimer /></div>
    </Card>
  );
}
