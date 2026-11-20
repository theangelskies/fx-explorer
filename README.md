# fx-explorer

A small Python app that downloads currency exchange rates from the [Frankfurter API](https://frankfurter.dev), checks and cleans them, and stores them in a PostgreSQL database running in Docker.

You can run it as often as you like. Importing the same data twice never creates duplicates.

## Why Frankfurter?

- Free, with no API key and no monthly quota.
- Rates come from central banks and other official sources (112 of them), blended into one value per day.
- Long history: some currencies go back to 1948.
- 200 active currencies, precious metals and crypto assets (258 including legacy ones).
- A clear, documented API with an OpenAPI spec.

## How it works

```
Frankfurter API ──► client ──► parser ──► repository ──► PostgreSQL
                    HTTP,      validate    SQL, upserts
                    retries    & clean
                         ▲                      ▲
                         └───── importer ───────┘   (runs the steps in order)
                                    ▲
                                   CLI
```

| Part | Job |
|---|---|
| `frankfurter/client.py` | Talks to the API: timeouts, retries with backoff, a polite delay between requests. |
| `frankfurter/schemas.py` | Describes the raw JSON the API returns. |
| `parsing/` | Turns raw records into clean ones. Bad records are set aside with a reason instead of crashing the run. |
| `domain/models.py` | The clean `Currency` and `ExchangeRate` objects. |
| `repositories/` | The only code that writes SQL. |
| `importer.py` | Runs one import from start to finish and records a summary. |
| `__main__.py` | The command line interface. |

Each part only does its own job: the client never touches the database, the repositories never make HTTP calls, and the importer contains neither SQL nor HTTP details.

## Tech stack

Python 3.12 (managed with [uv](https://docs.astral.sh/uv/)), httpx, pydantic v2, psycopg 3, Alembic (plain SQL migrations), pytest and respx, PostgreSQL 16 in Docker Compose.

## Getting started

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you if needed)
- Docker with Docker Compose

### Setup

```bash
cp .env.example .env          # then edit it (see below)
docker compose up -d          # starts PostgreSQL on port 5433
uv sync                       # installs dependencies
uv run alembic upgrade head   # creates the tables
```

In `.env`, replace `change_me` with a password of your choice **in all three places**: `POSTGRES_PASSWORD`, `DATABASE_URL` and `TEST_DATABASE_URL`. The first start can take a minute while the databases are created.

### Importing rates

```bash
# One day, chosen currencies
uv run python -m fx_explorer import --base EUR --quotes USD,GBP --date 2026-11-20

# A date range (--to defaults to today)
uv run python -m fx_explorer import --from 2026-01-01 --to 2026-11-20

# Everything new since the last import
uv run python -m fx_explorer import --incremental
```

If you leave out `--base` and `--quotes`, the defaults from `.env` are used. `--incremental` needs some data already stored. It starts 3 days before the newest stored date, because blended rates can be revised after the fact.

Every run logs a summary of how many records were fetched, inserted, updated, unchanged and rejected, and is also saved in the `import_runs` table. A run ends as `success`, `partial` (some records were rejected) or `failed` (with the error message saved).

### Configuration

Settings live in `.env`:

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | Connection string for the main database |
| `TEST_DATABASE_URL` | Connection string for the test database |
| `FRANKFURTER_BASE_URL` | API address |
| `HTTP_TIMEOUT_SECONDS` | Timeout per request (default 10) |
| `MAX_RETRIES` | How many times a failed request is retried (default 3) |
| `REQUEST_DELAY_SECONDS` | Pause between requests (default 1) |
| `DEFAULT_BASE` | Base currency (default EUR) |
| `DEFAULT_QUOTES` | Currencies to import by default (default USD,GBP,NGN,JPY,CHF,CAD) |

### Running the tests

```bash
uv run pytest
```

Client and parser tests use mocked HTTP responses, so they need no network. Integration tests use the separate `fx_explorer_test` database (from `docker compose up -d`) and are skipped if it isn't running. They refuse to run against any database whose name doesn't end in `_test`.

### Looking at the data

Open a SQL prompt inside the container:

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Rows and date range per currency:

```sql
SELECT quote, count(*) AS rows, min(rate_date) AS first_date, max(rate_date) AS last_date
FROM exchange_rates GROUP BY quote ORDER BY quote;
```

Latest EUR → NGN:

```sql
SELECT rate_date, rate FROM exchange_rates
WHERE base = 'EUR' AND quote = 'NGN' ORDER BY rate_date DESC LIMIT 1;
```

Biggest day-to-day moves (in percent):

```sql
WITH daily AS (
  SELECT rate_date, quote, rate,
         lag(rate) OVER (PARTITION BY base, quote ORDER BY rate_date) AS prev_rate
  FROM exchange_rates WHERE base = 'EUR'
)
SELECT rate_date, quote, prev_rate, rate,
       round((rate - prev_rate) / prev_rate * 100, 2) AS pct_change
FROM daily WHERE prev_rate IS NOT NULL
ORDER BY abs((rate - prev_rate) / prev_rate) DESC LIMIT 5;
```

## Importing safely more than once (idempotency)

Each rate is identified by three values: **date + base currency + quote currency**. This is the table's primary key, so the database itself refuses two rows with the same trio. When a rate is imported:

- If that combination is new, it is inserted.
- If it exists and the rate is unchanged, nothing is written.
- If it exists and the rate changed, it is updated (`updated_at` moves, `first_seen_at` stays).

This is done in SQL with `INSERT ... ON CONFLICT (rate_date, base, quote) DO UPDATE ... WHERE rate IS DISTINCT FROM new rate`. Running the same import twice therefore reports `inserted=0 updated=0` the second time. All rates in one batch are saved in a single transaction, so a failure half way never leaves partial data.

The inserted/updated counts come from `RETURNING (xmax = 0)`: PostgreSQL's hidden `xmax` column is 0 on a freshly inserted row and non-zero on a row updated by `ON CONFLICT`. Unchanged rows return nothing. This is a widely used PostgreSQL idiom rather than an officially documented guarantee, so the integration tests check it.

## Handling bad data

One bad record never stops an import. Records are rejected, counted and logged with a reason if, for example, they have a missing field, a rate that is zero or negative, an invalid date or an unknown currency code. If any record is rejected the run is marked `partial`.

Money is stored as `NUMERIC` in the database and handled as Python `Decimal`, never `float`, to avoid rounding errors. Dates are `datetime.date`.

## Surprises in the data

Things found while testing the real API:

- **Self-pairs are returned.** Asking for rates from EUR includes an EUR → EUR record with rate 1.0. The parser skips these (they are expected, so they are not counted as rejects).
- **Weekends have rates.** There is one record per calendar day, not just business days, and Saturday and Sunday values differ from Friday's. "Day-to-day change" therefore means calendar day.
- **Dates can be mixed.** In one "latest" response some currencies (such as crypto) are a day behind others. The newest date available is usually yesterday.
- **Unknown currency codes give an error (422)**, but a valid pair on a future date simply returns an empty list (200).
- **Errors have a body** like `{"status": 422, "message": "invalid currency: XXX"}`.
- **Some fields are empty.** For example, many crypto assets have no ISO numeric code, and one currency (CMD) has no symbol.
- **Rates can be revised**, because they are blended from several sources. That is why incremental imports overlap by a few days.
- **`/currencies` includes metals and crypto**, not only currencies, so the database stores a `type` for each.
- **No rate-limit headers** are sent. We still retry politely on 429 and 5xx, and wait between requests.

## Project layout

```
fx-explorer/
├── src/fx_explorer/      application code
├── migrations/           Alembic database migrations (raw SQL)
├── tests/                unit and integration tests
├── docker/               database init script (creates the test database)
├── docs/samples/         saved real API responses
├── docker-compose.yml    PostgreSQL 16
├── pyproject.toml
└── .env.example
```

## What I would do next

- Scheduled daily imports (cron or a task scheduler) using `--incremental`.
- Import several base currencies, not just EUR.
- Use the API's `providers` option to compare sources, or use a single central bank's own rates.
- A small query command or dashboard for charts of rates over time.
- Alerts when a rate changes by an unusual amount.
- Count skipped self-pairs in the run summary, and store reject reasons in the database instead of only in the log.
- Run the tests automatically in CI.
