# 0005. Long-range trends, computed by the server

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

The app is meant to support informed decisions, like "over 3 years, the euro has slowly lost
value against the dollar". Until now `GET /v1/pairs/{base}/{quote}` offered windows up to one
year, and the database held less than a year of rates.

Since the Phase 6 backfill we hold **2016-01-01 onwards** (23,516 rows). That's enough history
for multi-year views, but it raises four questions:

1. **Who computes the numbers?** A 5-year change, an annualised rate, volatility. The web app
   could compute them from raw rates, and so could the AI agent planned for Week 3. Three
   implementations would produce three slightly different answers, and a language model
   is not a calculator.
2. **How many points does a chart need?** 5 years of daily data is ~1,825 points per pair (our
   data has a rate for every calendar day, see `docs/frankfurter-notes.md`). A chart a few hundred
   pixels wide can't show that many, and sending them all wastes bandwidth.
3. **What if there isn't enough data?** Some numbers make no sense on little data: annualising
   a 5-day change, or volatility from 4 moves. And a pair may have less history than the window.
4. **How do we avoid misleading people?** These are reference rates, not prices you can trade
   at, and the past doesn't predict the future.

## Decision

**Extend `getPair` (additive, no breaking change; see `openapi.yaml`):**

- `window` gains `3y` (1,095 days), `5y` (1,825 days) and `max` (all stored history). The window
  still ends at the latest stored date.
- New `interval` (`day` | `week` | `month`) controls only the chart `series`. Defaults: `day` up
  to `1y`, `week` for `3y`/`5y`, `month` for `max`. A down-sampled point is the **last stored
  rate of each ISO week / calendar month**, so the final point is always the latest rate.
- New fields: `window_start`, `series_interval`, `first_available_date`, `data_points`, and a
  `trend` object.

**The server computes every trend number exactly**, with `Decimal` arithmetic on all stored daily
rates in the window (never on the down-sampled series), using formulas written in the contract:

| Field | Formula | `null` when |
|---|---|---|
| `change_pct` | `(last / first − 1) × 100` | n/a |
| `annualized_change_pct` | `((last / first) ^ (365.25 / days) − 1) × 100` | period < 365 days |
| `volatility_annualized_pct` | `stdev(ln(rateₜ / rateₜ₋₁)) × √365 × 100` (sample stdev) | < 20 daily returns |
| `biggest_rise` / `biggest_fall` | largest positive / negative `(rateₜ / rateₜ₋₁ − 1) × 100` | no such move |
| `moving_average_30` / `_200` | mean of the last 30 / 200 stored rates up to `period_end` | not enough rates |

Percentages are rounded to 4 decimal places. Rate-valued numbers keep rate precision (`change_abs`
exact, averages to 10 places), so a rate like 0.00029946 isn't rounded to 0.0003.

**Volatility uses √365, not √252.** √252 is the usual factor for trading-day data (about 252
trading days a year). Our data has one rate per calendar day, weekends included, so there are
~365 returns per year. Using √252 would understate annual volatility by about 17% (√365 / √252 ≈ 1.20).
This deliberately changes the original Week 2 spec, which said √252.

**Not enough data is a normal answer, not an error.** If the window is longer than the stored
history, the response is `200` with what exists; `trend.period_start` is later than `window_start`,
so a client can say "only 2 years of data for this pair". Numbers that would be meaningless on too
little data are `null` instead of a misleading value.

**Honesty.** The contract (in `getPair` and `PairTrend`) states that these are reference rates and
that past trends don't predict future rates. The UI must show this wherever trends appear.

## Alternatives considered

- **Compute trends in the web app (or the agent):** no API work, but every client re-implements the
  maths, the results drift apart, and floating-point JavaScript isn't exact (ADR 0004). An LLM
  "estimating" a 5-year change is worse still. Rejected: one exact implementation, many readers.
- **Always send daily points:** simplest, and lets clients zoom. But `max` would mean thousands of
  points per request for a chart that can't show them. Rejected; a client that needs daily data
  can still ask for `interval=day`.
- **Average each week/month instead of taking its last rate:** smoother, but produces values that
  never existed, and the last chart point would no longer equal the latest rate. Rejected.
- **Return 400 when the window is longer than the history:** strict, but it forces the client to
  know each pair's history first, and "show what we have" is what a user wants. Rejected.
- **Return 0 instead of `null` for numbers we can't compute:** easier for clients, but `0%`
  volatility is a real (and wrong) claim. Rejected; `null` means "not enough data".
- **Add a separate `/v1/pairs/{base}/{quote}/trend` endpoint:** cleaner separation, but the chart
  and the trend panel always appear together, so it would double the requests. Rejected for now.

## Consequences

- ✅ The web app and the Week 3 agent read the same exact numbers. Formulas are documented
  once, in the contract.
- ✅ Long windows stay small: `5y` is ~262 weekly points instead of ~1,825.
- ✅ Partial history and short periods are handled by explicit `null`s and dates, not errors.
- ⚠️ `getPair` does more work. 5 years is ~1,825 rows per pair, which is tiny for PostgreSQL;
  the pure-Python maths is O(n) per request. If it ever matters, results can be cached per
  (pair, window, latest date).
- ⚠️ Moving averages ignore the window (they're always the last 30/200 rates), so they're the
  same in every window. That's intentional, but the UI should label them clearly.
- ⚠️ Gaps (e.g. NGN weekends before 2022) make a "daily" return span more than one day. This is
  documented, not corrected; NGN volatility over early years is based on fewer, longer moves.
- ⚠️ The pair-detail tests and schemathesis fail until Phase 8 implements this. That's expected
  with contract-first: the contract changed, so the code is now wrong.
