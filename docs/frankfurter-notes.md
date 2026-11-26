# Frankfurter notes

Facts about the Frankfurter API v2 that this project relies on, **observed in our own imports**
(not assumed). Each entry says where it comes from. README.md ("Surprises in the data") has the
Week 1 findings; this file adds what the multi-year backfill (2026-11-26) showed.

## First available date per currency

Frankfurter publishes a `start_date` for every currency in `GET /currencies`, the first day
it has data for. The backfill never requests dates before it (`fx_explorer/backfill.py`).

| Code | Name | First available date (Frankfurter `start_date`) | Imported from |
|---|---|---|---|
| EUR | Euro (our base) | 1999-01-01 | n/a (base) |
| USD | United States Dollar | 1948-06-21 | 2016-01-01 |
| GBP | British Pound | 1949-12-21 | 2016-01-01 |
| CHF | Swiss Franc | 1949-12-21 | 2016-01-01 |
| CAD | Canadian Dollar | 1953-05-11 | 2016-01-01 |
| JPY | Japanese Yen | 1969-12-01 | 2016-01-01 |
| NGN | Nigerian Naira | 1999-01-04 | 2016-01-01 |

- **With EUR as the base, nothing exists before 1999-01-01**, however far back the quote
  currency goes. `--backfill --from 1995-01-01 --dry-run` shows this: the plan starts at 1999,
  and NGN gets its own request from 1999-01-04.
- We imported from 2016-01-01 (the requested backfill). Older years can be added any time with
  `--backfill --from 1999-01-01`. Re-running is safe.

## What the backfill imported (2016-01-01 .. 2026-11-26)

| Quote | Rows | First | Last |
|---|---|---|---|
| CAD, CHF, GBP, JPY, USD | 3,983 each | 2016-01-01 | 2026-11-26 |
| NGN | 3,601 | 2016-01-01 | 2026-11-25 |

Total `exchange_rates`: **23,516 rows**. 11 yearly requests, all `success`, about 30 s with the
1 s throttle. A second run: `inserted=0 updated=0 unchanged=23516`.

## Surprises

1. **Every calendar day has a rate, weekends included, in every year we imported**, for USD, GBP,
   JPY, CHF and CAD (366 rows in 2016/2020/2024, 365 otherwise). So a year is ~365 rows per
   currency, not ~250 business days. Size is still tiny: ~2,200 rows/year for 6 currencies.

2. **NGN had weekday-only data in early years.** Rows per year: 259 (2016), 283, 308, 310, 308,
   345 (2021), then complete from 2022. In 2016 the missing days are 53 Saturdays, 52 Sundays and
   only 2 weekdays. Long-range NGN statistics are therefore based on fewer points in early years.

3. **A range request can return a record dated BEFORE `from`.** When a currency has no rate on
   the first requested day, Frankfurter also returns its last earlier rate. Asking for
   2017-01-01..2017-12-31 returned an NGN record dated **2016-12-30** (rate 322.96), while the
   2016 request gives **322.75** for that same day. The same happened for 2018 (NGN, 2017-12-29).
   Storing both made yearly chunks overwrite each other on every run, so the importer now drops
   records outside the requested range (they're logged as "Ignored … outside the requested range").

4. **Rates are revised weeks later, not just days.** Re-importing 2026 changed 129 rates first
   imported on 2026-11-20/21, all dated between **2026-10-14 and 2026-11-20** (up to ~5 weeks
   back), across all six quotes. ⚠️ `--incremental` currently re-reads only the last 3 days
   (`INCREMENTAL_OVERLAP_DAYS`), so it would miss most such revisions. A periodic re-import of
   recent weeks, or a larger overlap, would catch them.

5. **`GET /currencies` returned 200 records** during the backfill, while the saved sample
   `docs/samples/f2_currencies_all.json` has 258 (it includes legacy currencies; see README).

## Unchanged from Week 1 (see README "Surprises in the data")

Self-pairs are returned, mixed dates in "latest" responses (NGN is often a day behind: our last
NGN rate is 2026-11-25), unknown codes give 422, future dates give an empty list, no rate-limit
headers.
