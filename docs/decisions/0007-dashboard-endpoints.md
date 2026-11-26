# 0007. Dashboard endpoints: biggest movers and latest import run

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

The dashboard design (`docs/ui-design.md` §4.1) needs two things the contract didn't offer
(gaps G1 and G2 in §8):

1. **"Biggest movers today":** which currencies moved most since their previous rate. The only
   way to get that from the existing contract would be one `getPair` call per currency, around
   200 calls, just to find the top 5.
2. **"Data freshness":** when the importer last ran and whether it worked. `Currency.latest_rate_date`
   tells you the newest *rate date*, but not *when* the import ran, or that the last run failed.

The UI is built only from the contract (ADR 0001), so it can't work around either gap. The
contract has to change first.

## Decision

Two additive, read-only operations (no breaking change, still `v1`):

**`GET /v1/movers`** (`listMovers`, tag Rates)

- For each quote stored against `base` (default `EUR`, unknown → 400): the latest stored rate,
  the stored rate before it, `change_abs` (exact) and `change_pct` (4 decimal places, half to even:
  the same rounding as `getPair`, from the same code in `analytics/trends.py`).
- **"Latest" is per quote** and every item has its own `rate_date`. Quotes aren't always updated on
  the same day (NGN is usually a day behind), and pretending they were would compare different days.
- Sorted by the **size** of the move (absolute `change_pct`), biggest first, ties by `quote`.
- `limit` 1–50 (default 5) and an optional `direction` (`up` | `down`).
- Response `{ "base": "EUR", "data": [ … ] }`. **No cursor**: it's a ranking of at most 50
  items, not a collection to page through. The `data` envelope still matches other lists.
- Quotes with fewer than two stored rates are left out (there's no move to report).

**`GET /v1/import-runs/latest`** (`getLatestImportRun`, new tag Imports)

- The most recently **started** import run, whatever its status (`running`, `success`, `partial`,
  `failed`), with start/finish times, counts and `error_message`. 404 if no import has ever run.
- It deliberately shows failures. A dashboard that only shows the last *successful* run would hide
  the fact that today's import broke.
- Only the latest run is exposed, not the whole history. That's all the dashboard needs; a
  paginated `/v1/import-runs` can be added later without changing this one.

## Alternatives considered

- **Compute movers in the UI from `getPair` per currency:** ~200 requests per dashboard view.
  Rejected.
- **Movers for a fixed calendar day ("today"):** simpler to explain, but quotes a day behind would
  vanish from the list or be compared across a weekend gap. Per-quote latest dates, shown in the
  UI, are honest. Rejected.
- **Paginate movers with a cursor:** a top-N ranking doesn't need paging, and a cursor over a list
  sorted by a computed value would be awkward. Rejected.
- **Put freshness into `/v1/health`:** health should answer "is the API up?" quickly and stay
  simple for monitoring tools; import status is business data. Rejected.
- **Expose the full `import_runs` history now:** more than the dashboard needs. Deferred.

## Consequences

- ✅ The dashboard gets movers in one small request and can show an honest freshness line
  ("imported 2 h ago ✓" or "last import failed").
- ✅ `change_pct` is computed and rounded by the same code as `getPair`, so the numbers agree.
- ⚠️ `error_message` is shown to clients. It comes from our importer (e.g.
  `RetriesExhaustedError: GET /rates failed after 4 attempts…`) and contains no secrets, but any
  future change to the importer's error text is now visible to users.
- ⚠️ Movers reads every stored rate for the base with a window function (~23,000 rows today).
  That's fast at this size; with far more history, a per-quote `LATERAL … LIMIT 2` using the
  `(base, quote, rate_date DESC)` index would scale better.
