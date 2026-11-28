/**
 * TanStack Query hooks: one per contract operation the pages use. Each hook gives a page
 * `isPending` / `isError` / `data`, so every page handles loading, error and success the same way.
 */
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api, type Schemas } from "./client";
import { call } from "./errors";
import type { paths } from "./schema";

export type Currency = Schemas["Currency"];
export type Rate = Schemas["Rate"];
export type RatesQuery = NonNullable<paths["/v1/rates"]["get"]["parameters"]["query"]>;

/**
 * listCurrencies, following next_cursor until the last page, so selects get the full list.
 * (With has_rates=true it's a handful of currencies, i.e. one request.)
 */
export function useCurrencies(options: { hasRates?: boolean } = {}) {
  return useQuery({
    queryKey: ["currencies", options],
    staleTime: 10 * 60_000,
    queryFn: async () => {
      const all: Currency[] = [];
      let cursor: string | undefined;
      do {
        const page = await call(
          api.GET("/v1/currencies", {
            params: {
              query: {
                limit: 200,
                cursor,
                has_rates: options.hasRates,
              },
            },
          }),
        );
        all.push(...page.data);
        cursor = page.page.next_cursor ?? undefined;
      } while (cursor);
      return all;
    },
  });
}

/** listRates for one page. Keeps showing the previous page while the next one loads. */
export function useRates(query: RatesQuery) {
  return useQuery({
    queryKey: ["rates", query],
    queryFn: () => call(api.GET("/v1/rates", { params: { query } })),
    placeholderData: keepPreviousData,
  });
}

export type PairDetail = Schemas["PairDetail"];
export type PairTrend = Schemas["PairTrend"];
export type Window = Schemas["Window"];
export type SeriesInterval = Schemas["SeriesInterval"];

/** getPair. Keeps the previous window on screen (dimmed) while a new one loads. */
export function usePair(
  base: string,
  quote: string,
  window: Window,
  interval?: SeriesInterval,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    enabled: options.enabled ?? true,
    queryKey: ["pair", base, quote, window, interval ?? "auto"],
    queryFn: () =>
      call(
        api.GET("/v1/pairs/{base}/{quote}", {
          params: { path: { base, quote }, query: { window, interval } },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

/** getCurrency (full names for headings). */
export function useCurrency(code: string) {
  return useQuery({
    queryKey: ["currency", code],
    queryFn: () => call(api.GET("/v1/currencies/{code}", { params: { path: { code } } })),
    staleTime: 10 * 60_000,
  });
}

export type Alert = Schemas["Alert"];
export type AlertCreate = Schemas["AlertCreate"];
export type AlertStatus = NonNullable<
  NonNullable<paths["/v1/alerts"]["get"]["parameters"]["query"]>["status"]
>;

/** listAlerts, page by page ("Load more" follows next_cursor). */
export function useAlerts(status?: AlertStatus) {
  return useInfiniteQuery({
    queryKey: ["alerts", status ?? "all"],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      call(api.GET("/v1/alerts", { params: { query: { status, cursor: pageParam, limit: 50 } } })),
    getNextPageParam: (last) => last.page.next_cursor ?? undefined,
  });
}

export type Mover = Schemas["Mover"];
export type ImportRun = Schemas["ImportRun"];

/** listMovers (ADR 0007): the biggest day-over-day moves, biggest first. */
export function useMovers(limit = 5) {
  return useQuery({
    queryKey: ["movers", limit],
    queryFn: () => call(api.GET("/v1/movers", { params: { query: { limit } } })),
  });
}

/** getLatestImportRun (ADR 0007): data freshness. A 404 means "no import yet". */
export function useLatestImportRun() {
  return useQuery({
    queryKey: ["import-runs", "latest"],
    queryFn: () => call(api.GET("/v1/import-runs/latest")),
    refetchInterval: 5 * 60_000,
  });
}
