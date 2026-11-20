# fx-explorer

A small Python app that downloads currency exchange rates from the [Frankfurter API](https://frankfurter.dev), checks and cleans them, and stores them in a PostgreSQL database running in Docker.

You can run it as often as you like. Importing the same data twice never creates duplicates.

## Why Frankfurter?

- Free, with no API key and no monthly quota.
- Rates come from central banks and other official sources (112 of them), blended into one value per day.
- Long history: some currencies go back to 1948.
- Rates for 160+ currencies, plus some precious metals and crypto assets.
- A clear, documented API with an OpenAPI spec.

## How it works

```
Frankfurter API  →  client  →  parser  →  repository  →  PostgreSQL
                    (HTTP,     (validate   (SQL, upsert)
                    retries)    & clean)
```

| Part | Job |
|---|---|
| `frankfurter/client.py` | Talks to the API: timeouts, retries with backoff, polite delay between requests. |
| `frankfurter/schemas.py` | Describes the raw JSON the API returns. |
| `parsing/rates.py` | Turns raw records into clean ones. Bad records are set aside with a reason instead of crashing the run. |
| `repositories/` | The only code that writes SQL. |
| `importer.py` | Runs one import from start to finish and records a summary. |
| `__main__.py` | The command line interface. |

Each part only does its own job: the client never touches the database, and the repositories never make HTTP calls.

## Tech stack

Python 3.12 (managed with [uv](https://docs.astral.sh/uv/)), httpx, pydantic v2, psycopg 3, Alembic (plain SQL migrations), pytest and respx, PostgreSQL 16 in Docker Compose.

## Getting started

### Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Docker with Docker Compose

### Setup

```bash
cp .env.example .env          # then edit the values if you need to
docker compose up -d          # starts PostgreSQL on port 5433
uv sync                       # installs dependencies
uv run alembic upgrade head   # creates the tables
```

### Importing rates

```bash
# One day, chosen currencies
uv run python -m fx_explorer import --base EUR --quotes USD,GBP --date 2026-11-20

# A date range
uv run python -m fx_explorer import --from 2026-11-01 --to 2026-11-20

# Everything new since the last import
uv run python -m fx_explorer import --incremental
```

`--incremental` starts 3 days before the latest stored date, because blended rates can be revised after the fact.

Every run prints a summary of how many records were fetched, inserted, updated and rejected, and is also saved in the `import_runs` table.

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
| `DEFAULT_QUOTES` | Currencies to import by default |

### Running the tests

```bash
uv run pytest
```

Unit tests use mocked HTTP responses, so they need no network. Integration tests use the separate `fx_explorer_test` database.

## Importing safely more than once (idempotency)

Each rate is identified by three values: **date + base currency + quote currency**. This is the table's primary key. When a rate is imported:

- If that combination is new, it is inserted.
- If it exists and the rate is unchanged, nothing happens.
- If it exists and the rate changed, it is updated.

So running the same import twice gives "0 inserted, 0 updated" the second time. All rates in one batch are saved in a single transaction, so a failure half way never leaves partial data.

## Handling bad data

One bad record never stops an import. Records are rejected, counted and logged with a reason if they have, for example, a missing field, a rate that is zero or negative, an invalid date or an unknown currency code. If any record is rejected, the run is marked `partial`.

Money is stored as `NUMERIC` in the database and handled as Python `Decimal`, never `float`, to avoid rounding errors.

## Surprises in the data

Things found while testing the real API:

- **Self-pairs are returned.** Asking for rates from EUR includes an EUR→EUR record with rate 1.0. The importer skips these.
- **Weekends have rates.** There is one record per calendar day, not just business days.
- **Dates can be mixed.** In one "latest" response some currencies (such as crypto) are a day behind others.
- **Unknown currency codes give an error (422)**, but a valid pair on a future date simply returns an empty list.
- **Some fields are empty.** For example, many crypto assets have no ISO numeric code.
- **Rates can be revised**, because they are blended from several sources. That is why incremental imports overlap by a few days.
- **The `/currencies` list includes metals and crypto**, not only currencies, so the database stores a `type` for each.

Real sample responses for each of these are saved in [docs/samples/](docs/samples/).

## Project layout

```
fx-explorer/
├── src/fx_explorer/      application code
├── migrations/           Alembic database migrations
├── tests/                unit and integration tests
├── docs/samples/         saved real API responses
├── docker-compose.yml    PostgreSQL 16
├── pyproject.toml
└── .env.example
```

## Ideas for next steps

- Scheduled daily imports (cron or a task scheduler).
- Support for other providers via the API's `providers` option.
- A small query command or dashboard for charts of rates over time.
- Alerts when a rate changes by an unusual amount.
- Running the tests automatically in CI.
