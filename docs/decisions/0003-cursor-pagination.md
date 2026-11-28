# 0003. Cursor (keyset) pagination for all lists

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

`GET /v1/rates` can return a lot of rows: one per day per currency pair. Our saved sample of the latest EUR rates for every quote currency (`docs/samples/d_eur_all.json`) has 200 rows, about one per currency. A year of that is tens of thousands of rows. Lists must be split into pages.

The classic way is **offset pagination**: `?page=3&size=50`, which becomes SQL `LIMIT 50 OFFSET 100`. It has two problems that hit time-series data hard:

1. **Rows shift while you page.** Our data changes under the reader. Imports add new days, and with the default "newest first" sort, new rows land at the *top* of the list. Suppose you read page 1 and then an import adds 200 new rows. Page 2 (`OFFSET 50`) now starts inside what used to be page 1, so you see **duplicates**. Delete-style changes cause the opposite: **gaps**, i.e. rows you never see.
2. **Deep pages get slow.** `OFFSET 10000` makes PostgreSQL read and throw away 10,000 rows before returning 50. The cost grows with the page number.

**Cursor (keyset) pagination** avoids both. Instead of "skip N rows", the next request says "continue after the last row I saw":

```sql
-- page 1
SELECT ... WHERE base = 'EUR' ORDER BY rate_date DESC, quote ASC LIMIT 51;
-- next page: start strictly after the last row of page 1 (2026-11-19, 'USD')
SELECT ... WHERE base = 'EUR'
  AND (rate_date < '2026-11-19' OR (rate_date = '2026-11-19' AND quote > 'USD'))
ORDER BY rate_date DESC, quote ASC LIMIT 51;
```

New rows added at the top don't move the position, because we aren't counting rows. And with an index on `(base, quote, rate_date)`, or a matching one for the sort, the database jumps straight to the starting point, so page 500 is as fast as page 1.

## Decision

Every list endpoint (`/v1/currencies`, `/v1/rates`, `/v1/alerts`) uses cursor pagination.

**Request:**
- `limit`: integer, default **50**, min **1**, max **200**. Outside that range → 400 (not silently clamped).
- `cursor`: optional string, taken from a previous response's `page.next_cursor`.

**Response:**

```json
{
  "data": [ ... ],
  "page": { "limit": 50, "next_cursor": "eyJkIjoiMjAyNi0xMS0xOSIsInEiOiJVU0QifQ" }
}
```

- `page.limit` echoes the limit that was used (useful when the default applied).
- `page.next_cursor` is a string, or `null` on the last page. It is always present.
- To know if there's another page, the server fetches `limit + 1` rows. If the extra row exists, it builds a cursor from the last *returned* row and drops the extra one.

**The cursor is opaque.** The server encodes the sort-key values of the last row (e.g. date + quote) as base64url JSON. Clients must treat it as a meaningless token. That lets us change its contents later without breaking anyone. A cursor that can't be decoded → **400** `invalid-parameters` on field `cursor`.

**Every sort order is total**, meaning it has a unique tie-breaker, so "after this row" is never ambiguous:

| Endpoint | Order | Tie-breaker |
|---|---|---|
| `/v1/currencies` | `iso_code` ascending | (already unique) |
| `/v1/rates` | `rate_date` desc (or asc with `sort=date_asc`) | `quote` ascending (base is fixed per request) |
| `/v1/alerts` | `created_at` descending | `id` |

**Same filters required:** a cursor is only meaningful together with the filters that produced it. Clients must send the same filters when following `next_cursor`. Changing filters means starting again without a cursor.

## Alternatives considered

- **Offset/page numbers (`?page=3`):** easy to understand and allows "jump to page 7" and "page 3 of 20". Rejected because of the duplicates/gaps and slow deep pages described above.
- **Transparent cursors (`?after_date=2026-11-19&after_quote=USD`):** debuggable, but clients start building them by hand. Then the sort key becomes part of the public API forever. Rejected.
- **Returning everything (no pagination):** fine for 258 currencies, but not for rates. We use one consistent pattern for all lists so the UI has one pagination component. Rejected.
- **Including a `total` count:** nice for "1,234 results", but `count(*)` scans every matching row on every request. Rejected for now. It can be added later as an additive change.

## Consequences

- ✅ Stable pages while imports run: no duplicates, no gaps.
- ✅ Constant-time pages with the right indexes (added in migration 002).
- ✅ One pagination shape for every list, so one UI component.
- ⚠️ No "jump to page N" and no "page X of Y". The UI offers "Next" (and remembers earlier cursors for "Previous").
- ⚠️ Each new sort option needs its own cursor logic and a supporting index, so we keep sort options few on purpose.
- ⚠️ Cursors aren't signed, so a client can tamper with one. That's harmless: the decoded values are only used as bound query parameters (never pasted into SQL), and bad values are rejected with 400.
