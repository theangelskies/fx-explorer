/**
 * Dashboard (docs/ui-design.md §4.1): "how are my currencies doing?" at a glance.
 * Operations: listCurrencies (key pairs), getPair (30d per pair, chosen window, 5y per pair),
 * listMovers, getLatestImportRun (ADR 0007).
 */
import { describeError } from "../api/errors";
import { useCurrencies } from "../api/queries";
import { Converter, Freshness, KpiCard, LongTermCard, MainChart, MoversCard } from "../components/dashboard/DashboardCards";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Skeleton } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";

export function DashboardPage() {
  // Key pairs = every currency with stored rates except the base, so nothing is hard-coded.
  const currencies = useCurrencies({ hasRates: true });
  const eur = currencies.data?.find((c) => c.iso_code === "EUR");
  const quotes = (currencies.data ?? []).filter((c) => c.iso_code !== "EUR").map((c) => c.iso_code);

  return (
    <>
      <PageHeader title="Dashboard">
        <Freshness latestRateDate={eur?.latest_rate_date ?? null} />
      </PageHeader>

      {currencies.isPending ? (
        <div role="status" aria-busy="true" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <span className="sr-only">Loading dashboard…</span>
          {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-36" />)}
        </div>
      ) : currencies.isError ? (
        <ErrorState problem={describeError(currencies.error)} onRetry={() => currencies.refetch()} />
      ) : quotes.length === 0 ? (
        <EmptyState
          title="No rates stored yet"
          hint={<>Run the backfill first: <code className="font-mono">uv run python -m fx_explorer import --backfill --from 2016-01-01</code></>}
        />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {quotes.map((q) => <KpiCard key={q} quote={q} />)}
          </div>
          <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
            <div className="min-w-0 space-y-4">
              <MainChart quotes={quotes} />
              <LongTermCard quotes={quotes} />
            </div>
            <div className="space-y-4">
              <MoversCard />
              <Converter quotes={quotes} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
