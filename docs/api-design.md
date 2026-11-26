# API design guidelines

The rules the fx-explorer REST API follows. They are short on purpose: each rule says **what** to do and **why**. When a rule and `openapi.yaml` disagree, fix one of them through a decision record in [`docs/decisions/`](decisions/). Never let them drift apart.

Sources: [RFC 9110 (HTTP semantics)](https://www.rfc-editor.org/rfc/rfc9110), [RFC 9457 (Problem Details)](https://www.rfc-editor.org/rfc/rfc9457), [RFC 3339 (timestamps)](https://www.rfc-editor.org/rfc/rfc3339), the [IETF Idempotency-Key draft](https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/), and the public API guidelines from Zalando, Google (AIP) and Microsoft.

---

## 1. Resources and URLs

| Rule | Example | Why |
|---|---|---|
| Paths name **things** (nouns), not actions (verbs). The HTTP method is the verb. | `POST /v1/alerts`, not `POST /v1/createAlert` | One URL per thing. The methods (GET, POST, DELETE…) already say what you are doing. |
| Collections are **plural**. | `/v1/currencies`, `/v1/currencies/USD` | Reads naturally: "the currencies collection, item USD". |
| Multi-word path segments are **kebab-case**. | `/v1/import-runs/latest` | URLs are case-insensitive in practice for humans; dashes are the web convention. |
| Query parameters and JSON fields are **snake_case**. | `?has_rates=true`, `"next_cursor"` | Matches the database and Python, so no renaming happens between layers. |
| Path parameters identify a resource; query parameters filter, sort or paginate it. | `/v1/pairs/EUR/USD?window=30d` | The path says *which* thing, the query says *how much / which part* of it. |
| No trailing slashes, no file extensions. | `/v1/rates`, not `/v1/rates/` or `/v1/rates.json` | One canonical URL per resource. |

**Currency codes** are 3–4 uppercase letters (`USD`, `XAU`, `USDT`). Our own data has 11 four-letter crypto codes, so a "3 letters" rule would be wrong. The API accepts codes exactly as stored (uppercase) and documents the pattern `^[A-Z]{3,4}$`.

## 2. Versioning

- Every path starts with **`/v1`**.
- **Additive** changes (a new endpoint, a new optional query parameter, a new response field) are allowed inside `v1`.
- **Breaking** changes (removing or renaming a field, changing a type, making an optional input required, changing a meaning) need `/v2`.
- Clients must **ignore response fields they don't know**. That rule is what makes additive changes safe.

*Why a URL prefix and not a header?* It is visible in every log line, curl command and browser tab, which makes it the easiest kind to learn and debug.

## 3. HTTP methods

| Method | Used for | Safe? | Idempotent? |
|---|---|---|---|
| `GET` | Read a resource or a list | yes | yes |
| `POST` | Create a new resource in a collection | no | **no** (unless an `Idempotency-Key` is sent; see ADR 0005) |
| `DELETE` | Remove a resource | no | yes |

- **Safe** = it changes nothing on the server. **Idempotent** = doing it twice has the same effect as doing it once.
- We don't use `PUT` or `PATCH` this week: alerts are created and deleted, never edited.
- A `GET` never changes data. (Computing `triggered` on an alert is reading, not changing.)

## 4. Status codes

Only these codes are used. Every error response has a Problem Details body (section 7).

| Code | Meaning | When we use it |
|---|---|---|
| **200 OK** | Success with a body | Any successful `GET`. **An empty list is still 200** with `"data": []`. "Nothing matched" is a valid answer, not an error. |
| **201 Created** | A new resource exists | Successful `POST /v1/alerts`. The body is the new alert, and the **`Location`** header holds its URL (`/v1/alerts/{id}`). |
| **204 No Content** | Success, nothing to return | Successful `DELETE`. There is no body at all. |
| **400 Bad Request** | The request itself is malformed | Invalid **query or path parameters**: unknown currency in a filter, bad date, `from` after `to`, `limit` out of range, an invalid cursor, a bad enum value. |
| **404 Not Found** | The thing doesn't exist | Unknown resource id or code (`/v1/currencies/XXX`), a pair with no data, an unknown route. |
| **405 Method Not Allowed** | Wrong method for the URL | Produced by the framework (e.g. `PUT /v1/alerts`). Listed for completeness so it also returns a Problem. |
| **409 Conflict** | Clashes with the current state | The same `Idempotency-Key` is still being processed by an earlier request. |
| **422 Unprocessable Content** | Well-formed JSON, but the values break the rules | Invalid **request body**: missing field, wrong format, `base` = `quote`, unknown currency. Also: an `Idempotency-Key` reused with a different body. |
| **500 Internal Server Error** | Our bug | Any unexpected exception. The body never contains stack traces or SQL. |
| **503 Service Unavailable** | A dependency is down | The database can't be reached. `GET /v1/health` reports it explicitly, and any other endpoint returns it too when the DB is down. |

**400 vs 422 (why the split?):** a 400 says "your URL is wrong", which a developer fixes in code. A 422 says "the data you typed into the form is wrong", which an end user fixes by editing fields. The UI can then treat 422 as "show the messages under the inputs". Details in [ADR 0002](decisions/0002-error-format-problem-details.md).

## 5. Filtering and sorting

- Filters are query parameters named after the field: `?type=crypto`, `?base=EUR`.
- **Lists of values** use one comma-separated parameter: `?quotes=USD,GBP,JPY` (OpenAPI `style: form, explode: false`).
- **Date ranges** use `from` and `to`, both **inclusive**, both `YYYY-MM-DD`. `from` must be ≤ `to`, otherwise 400.
- **Free-text search** uses `q`, matched case-insensitively against code and name.
- **Booleans** accept only `true` or `false`.
- **Sorting** uses one `sort` parameter with fixed values such as `date_desc` / `date_asc`. There is no free-form "sort by any column" because each allowed order needs a matching index.
- **Defaults are documented** in the contract (e.g. `base` defaults to `EUR`, `sort` to `date_desc`).
- **Unknown query parameters are ignored**, as most public APIs do. The downside: a typo like `?qoutes=USD` is silently ignored and you get all quotes back. The contract and the typed client are what protect against that.

## 6. Pagination

Every list endpoint is **cursor-paginated** with the same shape:

```json
{
  "data": [ { "...": "..." } ],
  "page": { "limit": 50, "next_cursor": "eyJkIjoiMjAyNi0xMS0xOSJ9" }
}
```

- `limit`: default **50**, allowed **1–200**. Out of range → 400 (we don't silently clamp it, so the client learns about its mistake).
- `cursor`: copy it from `page.next_cursor` to get the next page. It is **opaque**: clients must not decode or build it themselves.
- `next_cursor: null` means "this is the last page".
- There is **no total count** (it costs a full scan on every request).
- Every list has a **total order** (a unique tie-breaker), so no row is skipped or repeated.

Why cursors and not `?page=3`: [ADR 0003](decisions/0003-cursor-pagination.md).

## 7. Errors

All errors use **RFC 9457 Problem Details** with media type `application/problem+json`:

```json
{
  "type": "/problems/validation-error",
  "title": "Request body is invalid",
  "status": 422,
  "detail": "2 fields are invalid.",
  "instance": "/v1/alerts",
  "errors": [
    { "field": "threshold", "message": "Must be a number greater than 0 with at most 10 decimal places." },
    { "field": "quote", "message": "Must be different from base." }
  ]
}
```

- `type` identifies the *kind* of error, and clients branch on it (not on `title` text).
- `title` is short and the same for every error of that type. `detail` explains *this* occurrence.
- `errors` is present only for 400 and 422 validation problems, with **one entry per invalid field**. Messages are written for a person reading a form.
- **Every** error, including framework ones (unknown route, wrong method, unexpected crash), has this shape.

Full list of types: [ADR 0002](decisions/0002-error-format-problem-details.md).

## 8. Optional vs nullable fields

These are two different ideas:

- **Optional**: the field may be *left out*.
- **Nullable**: the field is present but its value may be `null` ("we know there is no value").

Our rules:

- **Responses: every documented field is always present.** If there is no value, it is `null`, never missing. (Example: `"iso_numeric": null` for most crypto assets.) This lets the UI write `currency.iso_numeric ?? "—"` without also checking whether the key exists.
- **Requests**: a field is either required, or optional with a documented default. `note` on an alert is optional *and* nullable: leaving it out and sending `null` mean the same thing ("no note").
- In OpenAPI 3.1, nullable is written `type: [string, "null"]` (the old 3.0 `nullable: true` is not used).

## 9. Dates and times

| Kind | Format | Example | Used for |
|---|---|---|---|
| Calendar date | ISO 8601 / RFC 3339 `full-date` | `"2026-11-20"` | `rate_date`, `from`, `to`, `start_date` |
| Timestamp | RFC 3339 `date-time`, **always UTC with `Z`** | `"2026-11-21T08:15:30Z"` | `created_at`, `updated_at` |

- A rate belongs to a **day**, not a moment, so it is a date with no time and no time zone.
- Timestamps are always UTC; the UI converts to the viewer's local time.
- Our data has one rate per calendar day, weekends included (see README, "Surprises in the data"). So "daily change" means calendar day.

## 10. Money and decimals

- Rates and thresholds are **JSON strings** holding a plain decimal number: `"1.1568"`, `"0.000017454"`, `"2126163"`.
- **No exponent notation** (`"1.7454E-5"` is never sent), no thousands separators, `.` as the decimal point.
- The server works with `Decimal` and PostgreSQL `NUMERIC` end to end, with no `float` anywhere.
- Inputs (`threshold`) are validated with a pattern, so `"1e3"`, `"1,5"` and `" 1"` are rejected with a 422.

Why strings and not numbers: [ADR 0004](decisions/0004-decimals-as-strings.md).

## 11. Headers

| Header | Direction | Use |
|---|---|---|
| `Content-Type: application/json` | response | Successful bodies |
| `Content-Type: application/problem+json` | response | Error bodies |
| `Location` | response | On 201, the URL of the created resource |
| `Idempotency-Key` | request | Optional on `POST /v1/alerts`, max 255 characters |
| `Idempotent-Replayed: true` | response | The response is a stored replay, not a fresh result |

## 12. Checklist for a new endpoint

1. Add it to `openapi.yaml` first, with `operationId`, `summary`, `tags`, examples and **all** error responses.
2. `npx @redocly/cli lint openapi.yaml` → 0 errors.
3. If it changes a rule here, write a decision record.
4. Implement it, test it, and run schemathesis against it.
