# 0004. Rates and thresholds are decimal strings in JSON

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

Inside our system, money is exact. The importer parses JSON with `Decimal`, the domain models use `Decimal`, and PostgreSQL stores `NUMERIC`. No `float` appears anywhere (README, "Handling bad data").

The API is where that guarantee could break. JSON itself allows numbers of any length, but almost every JSON *parser* turns them into a 64-bit binary floating-point number (IEEE 754 "double"). JavaScript's `JSON.parse` always does. A double holds about 15–17 significant digits and can't represent most decimal fractions exactly:

```js
JSON.parse('{"r": 0.1}').r + JSON.parse('{"r": 0.2}').r   // 0.30000000000000004
JSON.parse('{"r": 1.23456789012345678}').r                // 1.2345678901234568  (digits lost)
```

Our rates cover a very wide range. The samples in `docs/samples/` go from **0.000017454** (EUR → BTC) to **2126163** (EUR → IRR). Small values like the BTC one are where exponent notation and rounding errors creep in. It also matters for alerts: a user who sets an alert at `1.1568` expects the comparison to use exactly `1.1568`, not `1.15679999999999993`.

## Decision

Every decimal value is sent and received as a **JSON string** holding a plain decimal number:

```json
{ "rate": "1.1568", "threshold": "1.2000", "change_pct": "-0.42" }
```

Applies to: `rate`, `threshold`, `current_rate`, `change_abs`, `change_pct`, `stats.min/max/avg`, and series values.

**Format rules (in the contract):**
- Plain decimal notation only: digits, optional leading `-` (only where negative values make sense, e.g. `change_abs`), optional `.` with digits after it.
- **Never exponent notation.** `Decimal("0.000017454")` can print as `1.7454E-5` in Python, so the server formats with `format(value, "f")`.
- Outputs: `type: string` with a `pattern` and examples. (No `format: decimal`: it isn't a standard JSON Schema format, and test-data generators like schemathesis may reject unknown formats. The pattern does the real checking.)
- Input `threshold`: must match `^\d+(\.\d{1,10})?$`, be greater than 0, and have at most **10** decimal places. Violations → 422 with a readable message. Why 10: the smallest rate in our samples, EUR → BTC `0.000017454`, already has 9 decimal places. 6 would make alerts on crypto and metals impossible to set precisely.

**Rates are returned with the precision we store**, and not padded or rounded. **Computed values** (`avg`, `change_pct`) are rounded on the server to a fixed number of decimal places, defined in the contract, so the server, not each client, decides how they are rounded.

## Alternatives considered

- **JSON numbers (`"rate": 1.1568`):** the most natural to read, and fine for most values. But JavaScript clients silently lose precision, the loss is invisible until someone compares values, and fixing it later would be a breaking change. Rejected.
- **Integers in minor units (`"rate_micros": 1156800`):** common for amounts of money (Stripe sends cents). It doesn't suit exchange rates: they have no natural minor unit, and the scale we'd need varies from 9+ decimal places (BTC) to none (IRR). Rejected.
- **Numbers, plus telling clients to use a big-decimal JSON parser:** we can't control every client, and our own UI would need a special parser. Rejected.

## Consequences

- ✅ Exact values from the database to the screen, and in alert comparisons.
- ✅ The format is explicit and validated in the contract, so schemathesis checks it.
- ⚠️ Clients must convert before doing maths. The UI displays the string as-is wherever possible. For the converter and charts (where a tiny float error is invisible), it uses `Number(rate)` deliberately and only for display, never to send values back.
- ⚠️ Charts (Recharts) need numbers, so the UI maps series values with `Number(...)`.
- ⚠️ Sorting decimal strings as text is wrong (`"10" < "9"`), so any client-side sorting must convert first. The API sorts on the server using the real `NUMERIC` values.
- ⚠️ Slightly more verbose JSON and a few more lines of validation code.
