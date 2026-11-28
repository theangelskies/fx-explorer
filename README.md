# fx-explorer

A small Python app that downloads currency exchange rates from the [Frankfurter API](https://frankfurter.dev), checks and cleans them, and stores them in a PostgreSQL database running in Docker. On top of the stored data sits a **REST API** (designed contract-first in [`openapi.yaml`](openapi.yaml)), a **web dashboard** built only from that contract, and an **"Ask your data" analyst agent** that answers questions in plain English by calling that API.

You can run the importer as often as you like. Importing the same data twice never creates duplicates.

## Why Frankfurter?

- Free, with no API key and no monthly quota.
- Rates come from central banks and other official sources (112 of them), blended into one value per day.
- Long history: some currencies go back to 1948.
- 200 active currencies, precious metals and crypto assets (258 including legacy ones).
- A clear, documented API with an OpenAPI spec.

## How it works

```
Frankfurter API ──► client ──► parser ──► repository ──► PostgreSQL ◄── repository ◄── REST API ◄── web UI
                    HTTP,      validate    SQL, upserts                  SQL reads      FastAPI       React
                    retries    & clean                                                  (/v1/…)       (contract-only)
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
| `repositories/` | The only code that writes SQL (for the importer **and** the API). |
| `importer.py`, `backfill.py` | Runs one import, or plans a multi-year backfill in yearly chunks. |
| `__main__.py` | The command line interface. |
| `analytics/trends.py` | Pure trend maths (change, annualised change, volatility, moving averages), exact `Decimal`. |
| `api/` | The FastAPI app: routes, Problem Details errors, cursors, idempotency. |
| `web/` | The React app, built only from `openapi.yaml`. |
| `agent/` | The "Ask your data" agent: tools, prompt, the loop, the model wrapper, transcripts, CLI. |
| `evals/` | The agent's eval set, ground truth computed with SQL, the scorer and the runner. |

Each part only does its own job: the client never touches the database, the repositories never make HTTP calls, the importer contains neither SQL nor HTTP details, and the API never calls Frankfurter (it serves what the importer stored).

## Tech stack

- **Importer & API:** Python 3.12 (managed with [uv](https://docs.astral.sh/uv/)), httpx, pydantic v2, psycopg 3, Alembic (plain SQL migrations), FastAPI + uvicorn, PostgreSQL 16 in Docker Compose.
- **Tests & contract:** pytest, respx, schemathesis (property-based contract tests), PyYAML, Redocly CLI (lint + docs).
- **Agent:** the official `anthropic` SDK (Claude) or OpenRouter's OpenAI-style API (called with httpx); the agent loop is written by hand, no agent framework.
- **Web:** Node 22+, Vite, React, TypeScript, Tailwind CSS, React Router, TanStack Query, Recharts, openapi-typescript + openapi-fetch (types and client generated from the contract).

## Getting started

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you if needed)
- Docker with Docker Compose
- Node.js 22 or newer (for the web app and `npx @redocly/cli`)

### Setup

```bash
cp .env.example .env          # then edit it (see below)
docker compose up -d          # starts PostgreSQL on port 5433
uv sync                       # installs Python dependencies
uv run alembic upgrade head   # creates the tables
(cd web && npm install)       # installs the web app's dependencies
```

In `.env`, replace `change_me` with a password of your choice **in all three places**: `POSTGRES_PASSWORD`, `DATABASE_URL` and `TEST_DATABASE_URL`. The first start can take a minute while the databases are created.

### Importing rates

```bash
# Years of history, in yearly chunks (recommended first import)
uv run python -m fx_explorer import --backfill --from 2016-01-01
uv run python -m fx_explorer import --backfill --from 2016-01-01 --dry-run   # show the plan, call nothing

# One day, chosen currencies
uv run python -m fx_explorer import --base EUR --quotes USD,GBP --date 2026-11-20

# A date range (--to defaults to today)
uv run python -m fx_explorer import --from 2026-01-01 --to 2026-11-20

# Everything new since the last import
uv run python -m fx_explorer import --incremental
```

If you leave out `--base` and `--quotes`, the defaults from `.env` are used.

**`--backfill`** splits `--from … --to` (default today) into calendar years and imports them one by one, with the usual throttle between requests. It never asks for dates before a currency's first available date (Frankfurter's `start_date`); limited or skipped currencies are logged with the reason. Each yearly request is its own row in `import_runs`. If a year still fails after retries, the backfill carries on and finally lists the failed years with the exact command to re-run them (exit code 1). Re-running is safe: the second run reports `inserted=0`.

`--incremental` needs some data already stored. It starts 3 days before the newest stored date, because blended rates can be revised after the fact (but see the revision note in "Surprises in the data").

Every run logs a summary of how many records were fetched, inserted, updated, unchanged and rejected, and is also saved in the `import_runs` table. A run ends as `success`, `partial` (some records were rejected) or `failed` (with the error message saved).

### Data coverage

What the database held after the backfill of 2026-11-26 (`--backfill --from 2016-01-01`, base EUR):

| Currency | First available at Frankfurter | Stored from | Stored to | Rows |
|---|---|---|---|---|
| EUR (base) | 1999-01-01 | n/a | n/a | n/a |
| USD | 1948-06-21 | 2016-01-01 | 2026-11-26 | 3,983 |
| GBP | 1949-12-21 | 2016-01-01 | 2026-11-26 | 3,983 |
| CHF | 1949-12-21 | 2016-01-01 | 2026-11-26 | 3,983 |
| CAD | 1953-05-11 | 2016-01-01 | 2026-11-26 | 3,983 |
| JPY | 1969-12-01 | 2016-01-01 | 2026-11-26 | 3,983 |
| NGN | 1999-01-04 | 2016-01-01 | 2026-11-25 | 3,601 |

**23,516 rows in total.** There's a rate for every calendar day (weekends included) for every currency except NGN, which had weekday-only data until 2021. With EUR as the base nothing exists before 1999-01-01. Details and other findings: [`docs/frankfurter-notes.md`](docs/frankfurter-notes.md).

To extend or refresh the history (safe to repeat):

```bash
uv run python -m fx_explorer import --backfill --from 1999-01-01          # everything EUR-based
uv run python -m fx_explorer import --backfill --quotes SLE --from 2016-01-01   # add a currency
```

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
| `AGENT_PROVIDER` | Which service runs the model: `anthropic` (default) or `openrouter` |
| `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` | **Secrets.** Only in `.env`, never committed, logged or printed |
| `AGENT_MODEL` / `AGENT_DEV_MODEL` | Model for normal use / for the cheaper `--dev` tries (defaults `claude-sonnet-5-5` / `claude-haiku-5-5`) |
| `AGENT_MAX_STEPS` | Safety stop: at most this many model calls per question (default 8) |
| `AGENT_API_BASE_URL` | The API the agent's tools call (default `http://localhost:8000`) |
| `AGENT_FAKE` | `1` = the API's agent endpoints use a fake model (tests, schemathesis, demos). Never in production |

## Week 2: API & web UI

Built **contract-first**: [`openapi.yaml`](openapi.yaml) (OpenAPI 3.1) was written and linted before any API code, and it's the source of truth. If code and contract disagree, the code is wrong ([ADR 0001](docs/decisions/0001-contract-first-openapi.md)). The web app is built only from the contract: its TypeScript types are generated from it.

- **Contract:** [`openapi.yaml`](openapi.yaml) · rendered docs: [`docs/api.html`](docs/api.html) (open in a browser; rebuild with `npx @redocly/cli build-docs openapi.yaml -o docs/api.html`)
- **Design guides:** [`docs/api-design.md`](docs/api-design.md) (API rules) · [`docs/ui-design.md`](docs/ui-design.md) (design system, wireframes, states)

### Running the API

```bash
uv run alembic upgrade head                        # once, after pulling new migrations
uv run uvicorn fx_explorer.api.app:app --reload    # http://localhost:8000
```

- `/docs`: Swagger UI for the contract · `/openapi.yaml`: the contract itself (FastAPI's auto-generated spec is switched off, so there is only one).
- Every error is RFC 9457 Problem Details (`application/problem+json`): 400 for bad query/path parameters, 422 for a bad request body, with one `{field, message}` per invalid field ([ADR 0002](docs/decisions/0002-error-format-problem-details.md)).
- The API starts even if the database is down; it then answers 503.

### Running the web app

```bash
cd web
npm run dev        # http://localhost:5173 (needs the API running; /v1 is proxied to :8000)
npm run build      # regenerates the API types from ../openapi.yaml, type-checks, builds to dist/
npm run gen:api    # only regenerate src/api/schema.d.ts from the contract
```

Pages: **Dashboard** (`/`), **Rates explorer** (`/rates`), **Pair detail** (`/pairs/EUR/USD`), **Alerts** (`/alerts`). Light/dark mode follows your system (toggle in the header). Press **`?`** for keyboard shortcuts, **`/`** to jump to a pair.

### Endpoints

All under `/v1`. Decimals are strings (`"1.0948"`) to keep exact precision ([ADR 0004](docs/decisions/0004-decimals-as-strings.md)); lists use opaque cursor pagination ([ADR 0003](docs/decisions/0003-cursor-pagination.md)).

| Method | Path | operationId | What it does |
|---|---|---|---|
| GET | `/v1/health` | `getHealth` | API and database status (503 if the DB is down) |
| GET | `/v1/currencies` | `listCurrencies` | Currencies, filter by `type`, `q`, `has_rates`; cursor pages |
| GET | `/v1/currencies/{code}` | `getCurrency` | One currency, with `rates_count` and `latest_rate_date` |
| GET | `/v1/rates` | `listRates` | Daily rates: `base`, `quotes`, `from`/`to`, `sort`; cursor pages |
| GET | `/v1/pairs/{base}/{quote}` | `getPair` | Pair summary over `7d`…`5y`/`max`: latest, stats, **trend** (change, annualised, volatility, biggest moves, MA30/200), chart series (daily/weekly/monthly) |
| GET | `/v1/movers` | `listMovers` | Biggest day-over-day moves, latest day per currency |
| GET | `/v1/import-runs/latest` | `getLatestImportRun` | The most recent import run (data freshness) |
| POST | `/v1/alerts` | `createAlert` | Create a rate alert (422 field errors; optional `Idempotency-Key`) |
| GET | `/v1/alerts` | `listAlerts` | Alerts, filter `status=triggered\|waiting`; cursor pages |
| GET | `/v1/alerts/{id}` | `getAlert` | One alert with live `current_rate` and `triggered` |
| DELETE | `/v1/alerts/{id}` | `deleteAlert` | Delete an alert (204, then 404) |
| POST | `/v1/agent/ask` | `askAgent` | Ask the analyst agent a question; answer, sources, steps, pending action (Week 3) |
| POST | `/v1/agent/actions/{id}/confirm` | `confirmAgentAction` | Create the alert the agent proposed (idempotent) |
| POST | `/v1/agent/actions/{id}/cancel` | `cancelAgentAction` | Drop the proposed alert (204) |

**Idempotent alert creation** ([ADR 0006](docs/decisions/0006-idempotent-alert-creation.md)): send `Idempotency-Key: <uuid>` and retrying the same request returns the stored `201` (same alert) with `Idempotent-Replayed: true` instead of creating a duplicate; the same key with a different body gives 422, and while the first request is still running, 409.

**Trends are computed by the API**, exactly, so every client shows the same numbers ([ADR 0005](docs/decisions/0005-long-range-trends.md)). These are reference rates; past trends don't predict future rates.

### Decision records

| ADR | Decision |
|---|---|
| [0001](docs/decisions/0001-contract-first-openapi.md) | Contract-first API with OpenAPI 3.1, Redocly lint and contract tests |
| [0002](docs/decisions/0002-error-format-problem-details.md) | Errors as RFC 9457 Problem Details; 400 for parameters, 422 for bodies |
| [0003](docs/decisions/0003-cursor-pagination.md) | Opaque cursor (keyset) pagination for all lists |
| [0004](docs/decisions/0004-decimals-as-strings.md) | Rates and thresholds as decimal strings (threshold: up to 10 decimal places) |
| [0005](docs/decisions/0005-long-range-trends.md) | Long-range windows and server-side trend statistics (volatility uses √365) |
| [0006](docs/decisions/0006-idempotent-alert-creation.md) | Idempotent alert creation with an `Idempotency-Key` |
| [0007](docs/decisions/0007-dashboard-endpoints.md) | Dashboard endpoints: biggest movers and latest import run |
| [0008](docs/decisions/0008-tool-using-agent.md) | A tool-using agent: a hand-written loop over our own API (not the database, not a framework) |
| [0009](docs/decisions/0009-agent-endpoint.md) | Agent endpoint: conversations and pending actions in PostgreSQL; confirm is idempotent |

Template for new ones: [`docs/decisions/0000-template.md`](docs/decisions/0000-template.md).

### Screenshots

_Placeholders: add the images to `docs/screenshots/` and link them here._

| Screen | File to add |
|---|---|
| Dashboard (light) | `docs/screenshots/dashboard-light.png` |
| Dashboard (dark) | `docs/screenshots/dashboard-dark.png` |
| Pair detail, 5 years, comparing EUR/USD with EUR/GBP | `docs/screenshots/pair-5y-compare.png` |
| Rates explorer with filters and pagination | `docs/screenshots/rates-explorer.png` |
| Alert form showing the API's 422 messages | `docs/screenshots/alerts-422.png` |

### Demo checklist

With the API (`uv run uvicorn fx_explorer.api.app:app --reload`) and the web app (`cd web && npm run dev`) running, open http://localhost:5173:

1. **List with filter + pagination:** **Rates** → add quotes USD and NGN, From `2026-11-01`, **Apply** → the URL holds the filters → **Next ›** / **‹ Previous** (no duplicates; changing a filter returns to page 1).
2. **Detail view with 5-year trend:** click **EUR/USD** → press **6** (5y) → weekly chart, trend panel (−2.78 %, −0.56 %/year, volatility ~5 %, biggest rise/fall, MA30 vs MA200, "based on 1,825 daily rates"), disclaimer. Try **Compare with → EUR/GBP** and drag the zoom bar.
3. **Form with API validation errors:** **Alerts** → base EUR, quote EUR, threshold `1,5` → **Create alert** → the API's messages appear under Quote and Threshold.
4. **Empty state:** **Rates** → From `2030-01-01`, To `2030-12-31` → "No rates match these filters" with the import command.
5. **Error state:** stop the API → **Retry**-able "Can't reach the API" in the page, plus the banner within a minute. Start it again → **Retry** works.
6. **Partial history:** `uv run python -m fx_explorer import --backfill --quotes SLE --from 2016-01-01`, then open `/pairs/EUR/SLE?window=5y` → "Only … of data for EUR/SLE (since …); you asked for 5 years." (SLE data starts in 2022.) (`max` never shows this: it always starts at the first available date.)
7. **Idempotent replay:** in a terminal, run the same request twice:
   ```bash
   curl -i -X POST localhost:8000/v1/alerts -H 'Content-Type: application/json' \
     -H 'Idempotency-Key: demo-1' -d '{"base":"EUR","quote":"USD","condition":"above","threshold":"1.2"}'
   ```
   The second response has the **same id** and `idempotent-replayed: true`; **Alerts** shows one alert. Change the threshold with the same key → 422 "key reused with a different request".

### Running the tests

```bash
uv run pytest                                   # unit, integration and in-process contract tests
npx @redocly/cli lint openapi.yaml              # the contract: 0 errors expected
uv run schemathesis run openapi.yaml --url http://localhost:8000   # contract tests against a running API (see the warning below)
uv run pytest tests/agent tests/evals                              # the agent and the eval scorer (no API calls, no cost)
(cd web && npm run build)                       # the web app type-checks against the contract
```

Client and parser tests use mocked HTTP responses, so they need no network. Integration tests use the separate `fx_explorer_test` database (from `docker compose up -d`) and are skipped if it isn't running. They refuse to run against any database whose name doesn't end in `_test`.

`tests/api/test_contract.py` runs schemathesis in-process against every operation. [`schemathesis.toml`](schemathesis.toml) lists, with reasons, the few rules a JSON Schema can't express (a cursor must be one we issued, a currency must exist, an idempotent replay may point at a deleted alert).

⚠️ A schemathesis run against your **main** database's API sends real `POST /v1/alerts` requests and leaves idempotency keys behind. Either add `--exclude-operation-id createAlert`, or run the API against the test database: `(set -a; . ./.env; set +a; DATABASE_URL="$TEST_DATABASE_URL" AGENT_FAKE=1 uv run uvicorn fx_explorer.api.app:app --port 8001)`. `AGENT_FAKE=1` matters: without it schemathesis would call the real language model on every `/v1/agent/ask` test.

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

## Week 3: Ask your data

An **analyst agent** inside the app. You ask a question in plain English ("How has EUR→NGN changed over the last 3 years?"); it decides which of **this app's own API endpoints** to call, reads the results, repeats until it has enough, and answers **with its sources**. It can also *propose* a rate alert, but it never creates one without your explicit yes. Design: [`docs/agent-design.md`](docs/agent-design.md) · decisions: [ADR 0008](docs/decisions/0008-tool-using-agent.md), [ADR 0009](docs/decisions/0009-agent-endpoint.md).

### The loop

The model can only **ask** for tools; our code runs them. It only knows what the tools return.

```
                 ┌──────────────────────────────────────────────┐
                 │                                              │
 question ─► [ model thinks ] ──text only──► ANSWER (with sources)│
                 │   ▲                                          │
        tool_use │   │ tool_result                              │
                 ▼   │                                          │
           [ our loop runs the tool ] ──HTTP──► our API ──► DB   │
                 │                                              │
                 └─ steps == AGENT_MAX_STEPS? ──yes──► "I couldn't finish…"
```

The code is [`agent/loop.py`](src/fx_explorer/agent/loop.py) (about 100 lines of logic). The conversation history only ever grows: earlier turns are never edited.

### Tools

Each tool is one existing endpoint (a test checks every tool's input schema against `openapi.yaml`, so they can't drift):

| Tool | Endpoint | Kind |
|---|---|---|
| `get_pair_trend(base, quote, window)` | `GET /v1/pairs/{base}/{quote}` | read |
| `list_rates(base, quotes, from, to, limit)` | `GET /v1/rates` | read |
| `get_movers(base)` | `GET /v1/movers` | read |
| `list_currencies(q)` | `GET /v1/currencies` | read |
| `list_alerts(status)` | `GET /v1/alerts` | read |
| `create_alert(base, quote, condition, threshold, note)` | `POST /v1/alerts` | **write: needs confirmation** |

### Safety rules

- **Every number comes from a tool result** in that conversation. Long-range statistics (changes over years, volatility…) are never computed by the model: it quotes what the API returns. Simple arithmetic (amount × rate) is allowed and must show the rate used.
- **No writes without confirmation.** When the model calls `create_alert`, the loop does **not** run it. It records a *pending action* and tells the model "not executed, ask the user". Only a human pressing Confirm creates the alert, using the action id as the `Idempotency-Key` (a double click or retry can never create two alerts). The eval never confirms.
- **Tools call the API over HTTP, never the database.**
- **No predictions, no financial advice.** Trend answers say that past trends don't predict future rates.
- **Secrets** live only in `.env`; the key is never printed, logged or stored in a transcript.

### How to run

```bash
# 1. in .env: AGENT_PROVIDER=anthropic + ANTHROPIC_API_KEY, or AGENT_PROVIDER=openrouter + OPENROUTER_API_KEY
#    and AGENT_MODEL (see Configuration). The API must be running (it is what the tools call):
uv run uvicorn fx_explorer.api.app:app --reload
uv run alembic upgrade head          # once: creates the agent_conversations / agent_actions tables

# 2. from the terminal
uv run python -m fx_explorer ask "How has EUR to NGN moved over the last 3 years?"
uv run python -m fx_explorer chat --verbose --dev    # several questions; asks "Confirm? [y/N]" for alerts
#    --verbose prints every step (tool, request, result, tokens); --dev uses AGENT_DEV_MODEL.
#    Every run is saved as JSON in logs/agent/ (gitignored).

# 3. in the web app: http://localhost:5173/ask (npm run dev in web/), or the "Ask your data" card on the dashboard
```

Over HTTP (conversation id and the pending action's id come from the answers):

```bash
curl -s -X POST localhost:8000/v1/agent/ask -H 'Content-Type: application/json' \
  -d '{"question": "Alert me if EUR to USD goes above 1.20", "conversation_id": null}'
curl -s -X POST localhost:8000/v1/agent/actions/<pending_action.id>/confirm     # creates the alert (201; repeating it replays the same alert)
curl -s -X POST localhost:8000/v1/agent/actions/<pending_action.id>/cancel      # or drop it (204)
```

No key, no cost: `AGENT_FAKE=1 uv run uvicorn fx_explorer.api.app:app` serves the same endpoints with a fake model. (With a real database, confirming its pretend alert creates a real alert.)

### Evaluating it

[`evals/`](evals/README.md) holds 18 questions in 7 categories (single fact, trend, comparison, conversion, missing data, refusal, writes). The correct answers are **computed from the database with SQL** (`evals/build_expected.py`), not typed by hand. `evals/run.py` runs the real agent on every question (fresh conversation each, writes never confirmed) and writes a dated report to `evals/results/`.

```bash
uv run python evals/run.py --label baseline
```

**Latest score and cost: not recorded yet.** The eval harness and scorer are built and unit-tested, but no run with a real model has been done, so there is no baseline to show. When there is, it goes here: `score N/18 · cost $X.XX · report: evals/results/<file>.md`. Only the system prompt and tool descriptions may be changed to improve a score, never the expected answers.

### Known limitations

- **Reference rates only** (blended from central banks), not bank or card quotes, and only EUR-based pairs: there are no cross rates (USD→GBP) and no conversion endpoint.
- **No news:** it can describe what a rate did, but not explain why.
- **No predictions and no financial advice**, by design.
- History in the database starts at 2016-01-01 unless you backfill further; the agent says so when you ask about earlier dates.
- The Ask page shows time-based hints while waiting, not the agent's live steps (the API answers in one go).
- Answer quality depends on the model. Free or small models call tools less reliably than Claude; check the sources.
- Conversations are capped at 20 questions and are not yet deleted automatically.

## Importing safely more than once (idempotency)

Each rate is identified by three values: **date + base currency + quote currency**. This is the table's primary key, so the database itself refuses two rows with the same trio. When a rate is imported:

- If that combination is new, it is inserted.
- If it exists and the rate is unchanged, nothing is written.
- If it exists and the rate changed, it is updated (`updated_at` moves, `first_seen_at` stays).

This is done in SQL with `INSERT ... ON CONFLICT (rate_date, base, quote) DO UPDATE ... WHERE rate IS DISTINCT FROM new rate`. Running the same import twice therefore reports `inserted=0 updated=0` the second time. All rates in one batch are saved in a single transaction, so a failure half way never leaves partial data.

The inserted/updated counts come from `RETURNING (xmax = 0)`: PostgreSQL's hidden `xmax` column is 0 on a freshly inserted row and non-zero on a row updated by `ON CONFLICT`. Unchanged rows return nothing. This is a widely used PostgreSQL idiom rather than an officially documented guarantee, so the integration tests check it.

The importer only stores rates dated **inside the range it asked for**: Frankfurter sometimes adds a currency's last rate *before* `from` (see below), and storing it made yearly backfill chunks overwrite each other.

(The API's `POST /v1/alerts` has its own, different kind of idempotency, via the `Idempotency-Key` header; see ADR 0006.)

## Handling bad data

One bad record never stops an import. Records are rejected, counted and logged with a reason if, for example, they have a missing field, a rate that is zero or negative, an invalid date or an unknown currency code. If any record is rejected the run is marked `partial`.

Money is stored as `NUMERIC` in the database and handled as Python `Decimal`, never `float`, to avoid rounding errors (including in the API's trend maths). The API sends decimals as strings, and the web app displays those strings as they are. Dates are `datetime.date`.

## Surprises in the data

Things found while testing the real API:

- **Self-pairs are returned.** Asking for rates from EUR includes an EUR → EUR record with rate 1.0. The parser skips these (they are expected, so they are not counted as rejects).
- **Weekends have rates.** There is one record per calendar day, not just business days, and Saturday and Sunday values differ from Friday's. "Day-to-day change" therefore means calendar day. (This is also why the API annualises volatility with √365, not √252.)
- **Dates can be mixed.** In one "latest" response some currencies (such as crypto) are a day behind others. The newest date available is usually yesterday.
- **Unknown currency codes give an error (422)**, but a valid pair on a future date simply returns an empty list (200).
- **Errors have a body** like `{"status": 422, "message": "invalid currency: XXX"}`.
- **Some fields are empty.** For example, many crypto assets have no ISO numeric code, and one currency (CMD) has no symbol.
- **Rates can be revised, weeks later.** They are blended from several sources. Re-importing 2026 changed 129 rates dated up to ~5 weeks before the first import, so the 3-day `--incremental` overlap misses most revisions (see Roadmap).
- **A range can include a rate from before `from`.** If a currency has no rate on the first requested day (NGN on a weekend), Frankfurter adds its last earlier rate, with a different value than that day's own answer. The importer ignores such records.
- **NGN had weekday-only data until 2021**; every other default currency has all calendar days.
- **`/currencies` includes metals and crypto**, not only currencies, so the database stores a `type` for each.
- **No rate-limit headers** are sent. We still retry politely on 429 and 5xx, and wait between requests.

More detail: [`docs/frankfurter-notes.md`](docs/frankfurter-notes.md).

## Project layout

```
fx-explorer/
├── openapi.yaml          THE API contract (OpenAPI 3.1, the source of truth)
├── redocly.yaml          contract lint config
├── schemathesis.toml     contract-test settings (with reasons)
├── src/fx_explorer/      importer, repositories, analytics, api/ (FastAPI), agent/ (Ask your data)
├── evals/                agent eval set, expected answers (SQL), scorer, runner, results/
├── migrations/           Alembic database migrations (raw SQL)
├── tests/                unit and integration tests; tests/api/ = endpoint + contract tests
├── web/                  React app (built only from openapi.yaml)
├── docs/
│   ├── api-design.md     API design guidelines
│   ├── ui-design.md      UI/UX design (design system, wireframes, states)
│   ├── agent-design.md   how the agent works (loop, tools, rules, confirmation)
│   ├── decisions/        decision records (ADRs 0001–0009)
│   ├── frankfurter-notes.md  observed Frankfurter facts and data coverage
│   ├── api.html          rendered contract docs
│   └── samples/          saved real API responses
├── docker/               database init script (creates the test database)
├── docker-compose.yml    PostgreSQL 16
├── pyproject.toml
└── .env.example
```

## Roadmap

**Done:** importer with idempotent upserts (Week 1) · multi-year backfill · contract-first REST API with Problem Details, cursor pagination, long-range trends and idempotent alerts · web dashboard (rates explorer, pair detail with trend panel and comparison, alerts, dashboard) · contract tests with schemathesis · the "Ask your data" agent (Week 3: tools, hand-written loop, CLI, API endpoints, web Ask page, eval harness).

**Next:**

- **Catch late revisions:** raise the `--incremental` overlap (e.g. 45 days) or re-import the last 6 weeks weekly; rates were seen revised ~5 weeks later.
- Scheduled daily imports (cron or a task scheduler) using `--incremental`, shown on the dashboard's freshness line.
- **Notify** when an alert triggers (email/web push), not only show it in the list.
- Expire old idempotency keys (e.g. after 24 h, as Stripe does) and scope keys per user once there are accounts.
- Import several base currencies, not just EUR; optionally server-side cross rates (would enable a USD → GBP converter).
- Use the API's `providers` option to compare sources, or use a single central bank's own rates.
- Count skipped self-pairs in the run summary, and store reject reasons in the database instead of only in the log.
- Update `ARCHITECTURE.md`, which still describes the Week 1 importer only.
- Run the tests, the contract lint, schemathesis and the web build automatically in CI.
- **Run the eval and improve the prompt** (Week 3, Phase 8): the harness is built and tested, but no real-model baseline has been recorded yet (needs a real API key).
- **Live progress for the Ask page:** a streaming variant of `askAgent` (server-sent events), so the UI can show the agent's real steps (contract gap G6).
- **Retention for agent data:** conversations and decided actions are never deleted yet; expire them (they contain users' questions).
- Refusal fallbacks (a second model when the first declines) and prompt-cache tuning for the Anthropic provider.
- Week 4: scheduled jobs that use the agent (for example a daily briefing as a plain workflow, not an agent).
