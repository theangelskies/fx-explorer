/**
 * Rates explorer (docs/ui-design.md §4.2): filter, browse and page through stored daily rates.
 * Operations: listCurrencies (filter options), listRates (table).
 */
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { ApiError, describeError } from "../api/errors";
import { useCurrencies, useRates, type Currency, type Rate, type RatesQuery } from "../api/queries";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { Pagination } from "../components/Pagination";
import { formatDate, formatDateTime, formatDecimal, formatRelative } from "../lib/format";

// ------------------------------------------------------------------ URL <-> filters
// All filters live in the URL (?base=EUR&quotes=USD,GBP&from=…), so a view can be shared,
// bookmarked and survives a refresh. Values are passed to the API as they are: the API
// validates them and we show its messages (no rules duplicated here).

type Sort = NonNullable<RatesQuery["sort"]>;
const LIMITS = [25, 50, 100, 200] as const;
const DEFAULTS = { base: "EUR", sort: "date_desc" as Sort, limit: 50 };

interface Filters {
  base: string;
  quotes: string[];
  from: string;
  to: string;
  sort: Sort;
  limit: number;
}

function readFilters(params: URLSearchParams): Filters {
  const limit = Number(params.get("limit"));
  return {
    base: params.get("base") || DEFAULTS.base,
    quotes: (params.get("quotes") ?? "").split(",").filter(Boolean),
    from: params.get("from") ?? "",
    to: params.get("to") ?? "",
    sort: params.get("sort") === "date_asc" ? "date_asc" : DEFAULTS.sort,
    limit: Number.isInteger(limit) && limit > 0 ? limit : DEFAULTS.limit,
  };
}

function writeFilters(f: Filters): URLSearchParams {
  const params = new URLSearchParams();
  if (f.base !== DEFAULTS.base) params.set("base", f.base);
  if (f.quotes.length) params.set("quotes", f.quotes.join(","));
  if (f.from) params.set("from", f.from);
  if (f.to) params.set("to", f.to);
  if (f.sort !== DEFAULTS.sort) params.set("sort", f.sort);
  if (f.limit !== DEFAULTS.limit) params.set("limit", String(f.limit));
  return params;
}

// ------------------------------------------------------------------------- page

export function RatesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = readFilters(searchParams);
  const filtersKey = searchParams.toString();

  // Cursor pagination: "Next" pushes next_cursor, "Previous" pops. The stack belongs to one set
  // of filters: when the filters (URL) change, it's ignored and we start at page 1, because a
  // cursor is only valid with the filters that produced it (ADR 0003).
  const [nav, setNav] = useState<{ key: string; cursors: string[] }>({ key: filtersKey, cursors: [] });
  const cursors = nav.key === filtersKey ? nav.cursors : [];
  const cursor = cursors.at(-1);

  const rates = useRates({
    base: filters.base,
    quotes: filters.quotes.length ? filters.quotes : undefined,
    from: filters.from || undefined,
    to: filters.to || undefined,
    sort: filters.sort,
    limit: filters.limit,
    cursor,
  });

  // A 400 lists the bad parameters: show each message under its filter, not as a page error.
  const paramErrors: Record<string, string> = {};
  if (rates.error instanceof ApiError && rates.error.status === 400) {
    for (const e of rates.error.problem.errors ?? []) paramErrors[e.field] = e.message;
  }
  const hasParamErrors = Object.keys(paramErrors).length > 0;

  const apply = (next: Filters) => setSearchParams(writeFilters(next));

  return (
    <>
      <PageHeader title="Rates" />
      <div className="space-y-4">
        <Card>
          {/* key: re-create the form (and its draft values) when the URL changes, e.g. Back. */}
          <FilterBar key={filtersKey} filters={filters} errors={paramErrors} onApply={apply} />
        </Card>

        <Card>
          {hasParamErrors ? (
            <p role="alert" className="text-sm text-down">
              {rates.error instanceof ApiError ? rates.error.problem.detail : ""} Fix the highlighted
              filters above.
            </p>
          ) : rates.isPending ? (
            <Loading label="Loading rates…" rows={8} />
          ) : rates.isError ? (
            <ErrorState problem={describeError(rates.error)} onRetry={() => rates.refetch()} />
          ) : rates.data.data.length === 0 ? (
            <EmptyState
              title="No rates match these filters"
              hint={
                <>
                  Try a wider date range or fewer filters, or import more data:{" "}
                  <code className="font-mono">uv run python -m fx_explorer import --incremental</code>
                  {filters.base !== "EUR" && (
                    <>
                      <br />
                      Rates are stored with EUR as the base; {filters.base} as a base may have no data.
                    </>
                  )}
                </>
              }
              action={
                cursors.length === 0 && (
                  <button
                    type="button"
                    className="text-sm text-accent underline underline-offset-2"
                    onClick={() => setSearchParams(new URLSearchParams())}
                  >
                    Reset filters
                  </button>
                )
              }
            />
          ) : (
            <>
              <RatesTable rates={rates.data.data} busy={rates.isPlaceholderData} />
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                <RowsPerPage
                  value={filters.limit}
                  onChange={(limit) => apply({ ...filters, limit })}
                />
                <Pagination
                  page={cursors.length + 1}
                  hasPrevious={cursors.length > 0}
                  hasNext={rates.data.page.next_cursor !== null}
                  busy={rates.isFetching}
                  onPrevious={() => setNav({ key: filtersKey, cursors: cursors.slice(0, -1) })}
                  onNext={() => {
                    const next = rates.data.page.next_cursor;
                    if (next) setNav({ key: filtersKey, cursors: [...cursors, next] });
                  }}
                />
              </div>
            </>
          )}
        </Card>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------- filters

const label = "mb-1 block text-xs font-medium text-muted";
const control =
  "w-full rounded-md border border-line-input bg-surface px-2.5 py-1.5 text-sm aria-[invalid=true]:border-down";

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} className="mt-1 text-xs text-down">
      {message}
    </p>
  );
}

interface FilterBarProps {
  filters: Filters;
  errors: Record<string, string>;
  onApply: (filters: Filters) => void;
}

function FilterBar({ filters, errors, onApply }: FilterBarProps) {
  const [draft, setDraft] = useState(filters);
  const currencies = useCurrencies({ hasRates: true });
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onApply(draft);
  };

  const options: Currency[] = currencies.data ?? [];
  const addable = options.filter((c) => c.iso_code !== draft.base && !draft.quotes.includes(c.iso_code));
  const err = (field: string) => ({
    "aria-invalid": errors[field] ? true : undefined,
    "aria-describedby": errors[field] ? `err-${field}` : undefined,
  });

  return (
    <form onSubmit={submit} className="grid gap-4 md:grid-cols-[8rem_1fr_10rem_10rem_11rem]" aria-label="Filter rates">
      <div>
        <label htmlFor="f-base" className={label}>
          Base
        </label>
        <select
          id="f-base"
          className={control}
          value={draft.base}
          onChange={(e) => set("base", e.target.value)}
          {...err("base")}
        >
          {/* keep the current value selectable even before/without the list */}
          {!options.some((c) => c.iso_code === draft.base) && <option value={draft.base}>{draft.base}</option>}
          {options.map((c) => (
            <option key={c.iso_code} value={c.iso_code}>
              {c.iso_code}
            </option>
          ))}
        </select>
        <FieldError id="err-base" message={errors.base} />
      </div>

      <div>
        <span id="f-quotes-label" className={label}>
          Quotes <span className="font-normal">(empty = all)</span>
        </span>
        <div
          role="group"
          aria-labelledby="f-quotes-label"
          {...err("quotes")}
          className="flex min-h-[34px] flex-wrap items-center gap-1.5 rounded-md border border-line-input px-1.5 py-1 aria-[invalid=true]:border-down"
        >
          {draft.quotes.map((code) => (
            <button
              key={code}
              type="button"
              onClick={() => set("quotes", draft.quotes.filter((q) => q !== code))}
              className="rounded bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent hover:bg-accent/20"
              aria-label={`Remove ${code}`}
            >
              {code} <span aria-hidden="true">×</span>
            </button>
          ))}
          <select
            aria-label="Add a quote currency"
            className="min-w-0 flex-1 bg-transparent px-1 py-0.5 text-sm"
            value=""
            disabled={addable.length === 0}
            onChange={(e) => e.target.value && set("quotes", [...draft.quotes, e.target.value])}
          >
            <option value="">{currencies.isPending ? "Loading…" : "+ add"}</option>
            {addable.map((c) => (
              <option key={c.iso_code} value={c.iso_code}>
                {c.iso_code} · {c.name}
              </option>
            ))}
          </select>
        </div>
        <FieldError id="err-quotes" message={errors.quotes} />
        {currencies.isError && (
          <p className="mt-1 text-xs text-down">
            Couldn't load the currency list.{" "}
            <button type="button" className="underline" onClick={() => currencies.refetch()}>
              Retry
            </button>
          </p>
        )}
      </div>

      <div>
        <label htmlFor="f-from" className={label}>
          From
        </label>
        <input id="f-from" type="date" className={control} value={draft.from}
          onChange={(e) => set("from", e.target.value)} {...err("from")} />
        <FieldError id="err-from" message={errors.from} />
      </div>

      <div>
        <label htmlFor="f-to" className={label}>
          To
        </label>
        <input id="f-to" type="date" className={control} value={draft.to}
          onChange={(e) => set("to", e.target.value)} {...err("to")} />
        <FieldError id="err-to" message={errors.to} />
      </div>

      <div>
        <label htmlFor="f-sort" className={label}>
          Sort
        </label>
        <select id="f-sort" className={control} value={draft.sort}
          onChange={(e) => set("sort", e.target.value as Sort)} {...err("sort")}>
          <option value="date_desc">Newest first</option>
          <option value="date_asc">Oldest first</option>
        </select>
        <FieldError id="err-sort" message={errors.sort} />
      </div>

      <div className="flex gap-2 md:col-span-5 md:justify-end">
        <button
          type="button"
          onClick={() => onApply({ ...DEFAULTS, quotes: [], from: "", to: "" })}
          className="rounded-md border border-line-input px-3 py-1.5 text-sm font-medium hover:bg-line/40"
        >
          Reset
        </button>
        <button type="submit" className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-on-accent hover:opacity-90">
          Apply
        </button>
      </div>
      <FieldError id="err-limit" message={errors.limit} />
      <FieldError id="err-cursor" message={errors.cursor} />
    </form>
  );
}

// ------------------------------------------------------------------------ table

function RatesTable({ rates, busy }: { rates: Rate[]; busy: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table aria-busy={busy} className={`w-full text-sm transition-opacity ${busy ? "opacity-60" : ""}`}>
        <caption className="sr-only">Daily exchange rates</caption>
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <th scope="col" className="py-2 pr-4 font-medium">Date</th>
            <th scope="col" className="py-2 pr-4 font-medium">Pair</th>
            <th scope="col" className="py-2 pr-4 text-right font-medium">Rate</th>
            <th scope="col" className="py-2 font-medium">Updated</th>
          </tr>
        </thead>
        <tbody>
          {rates.map((r) => (
            <tr key={`${r.rate_date}-${r.base}-${r.quote}`} className="border-b border-line/60 last:border-0 hover:bg-line/20">
              <td className="py-2 pr-4 whitespace-nowrap tabular-nums">{formatDate(r.rate_date)}</td>
              <td className="py-2 pr-4">
                <Link to={`/pairs/${r.base}/${r.quote}`} className="font-medium text-accent hover:underline">
                  {r.base}/{r.quote}
                </Link>
              </td>
              <td className="py-2 pr-4 text-right font-medium tabular-nums">{formatDecimal(r.rate)}</td>
              <td className="py-2 whitespace-nowrap text-muted" title={formatDateTime(r.updated_at)}>
                {formatRelative(r.updated_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RowsPerPage({ value, onChange }: { value: number; onChange: (limit: number) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm text-muted">
      Rows per page
      <select className="rounded-md border border-line-input bg-surface px-2 py-1 text-sm text-fg"
        value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {!LIMITS.includes(value as (typeof LIMITS)[number]) && <option value={value}>{value}</option>}
        {LIMITS.map((n) => (
          <option key={n} value={n}>{n}</option>
        ))}
      </select>
    </label>
  );
}
