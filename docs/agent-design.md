# Agent design: "Ask your data"

This document explains how the analyst agent works **before any code exists**. When this file and the code disagree, fix one of them on purpose (and record why in [`docs/decisions/0008-tool-using-agent.md`](decisions/0008-tool-using-agent.md)).

## 1. What the agent is for

You type a question in plain English ("How has EUR→NGN changed over the last 3 years?"). The agent decides which of **our own API endpoints** to call, reads the results, and answers with sources. It can also *propose* a rate alert, but it never creates one without an explicit yes.

## 2. Agent vs workflow

| | Workflow | Agent |
|---|---|---|
| Who decides the steps? | **The programmer**, in advance | **The model**, while running |
| Shape | Fixed sequence: A → B → C | A loop: look → act → look → act → … → answer |
| Predictable? | Very (same steps every time) | Less (steps depend on the question) |
| Cost per run | Known | Varies (more tool calls = more tokens) |
| Good for | Tasks whose steps never change | Tasks where *what to do next depends on what you just learned* |

**A daily briefing is a workflow.** Every morning the steps are identical: fetch movers → fetch the latest import run → fetch triggered alerts → write a summary. We know the plan, so we code the plan. No decisions needed.

**"Ask your data" needs an agent**, because the steps depend on the question:

- "What was EUR→USD on 2024-03-01?" → one call to `list_rates`.
- "How has EUR→NGN changed over 3 years?" → one call to `get_pair_trend`.
- "Which of my currencies moved most against the euro this year?" → one `get_pair_trend` call *per currency* (the model first has to learn which currencies we store), then compare.
- "Which bank should I use?" → no tool at all, just a polite refusal.

A fixed workflow would either do too much for easy questions or too little for hard ones. The model chooses.

## 3. The loop in plain English

Think of the model as a very well-read colleague who **cannot see our database**. It can only ask questions through a list of tools we give it, and it only knows what those tools return.

1. We send the model: the system prompt (rules), the conversation so far, the question, and the **list of tools** (name, description, input format).
2. The model replies with either
   - **text only** → it is done; that text is the answer, or
   - **one or more `tool_use` requests** ("please call `get_pair_trend` with base=EUR, quote=NGN, window=3y").
3. If it asked for tools, **our code** (not the model) calls the matching endpoint of our API over HTTP, and puts the result back into the conversation as a `tool_result`.
4. We call the model again with the longer conversation. Go to step 2.
5. Safety stop: after `AGENT_MAX_STEPS` (default 8) model calls we stop looping and say "I couldn't finish, here's what I found."

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

Key idea: **the model never touches anything.** It only *asks*; our loop *does*, and our loop decides whether the request is allowed.

## 4. Tools

Each tool is **one existing endpoint**. I checked every one in `openapi.yaml`: all exist, nothing needs inventing.

| Tool | Endpoint (operationId) | Params the model may send | Read/Write |
|---|---|---|---|
| `get_pair_trend(base, quote, window)` | `GET /v1/pairs/{base}/{quote}` (`getPair`) | `window` ∈ `7d, 30d, 90d, 1y, 3y, 5y, max` | read |
| `list_rates(base, quotes, from, to, limit)` | `GET /v1/rates` (`listRates`) | `base`, `quotes` (comma list, ≤50), `from`, `to` (`YYYY-MM-DD`), `limit` 1–200 | read |
| `get_movers(base)` | `GET /v1/movers` (`listMovers`) | `base` (also `limit` 1–50 and `direction` up/down exist) | read |
| `list_currencies(q)` | `GET /v1/currencies` (`listCurrencies`) | `q` (also `type`, `has_rates`, `limit`) | read |
| `list_alerts(status)` | `GET /v1/alerts` (`listAlerts`) | `status` ∈ `triggered, waiting` | read |
| `create_alert(base, quote, condition, threshold, note)` | `POST /v1/alerts` (`createAlert`) | `condition` ∈ `above, below`; `threshold` decimal string; `note` ≤200 chars | **write, confirmation required** |

Things worth knowing (not missing endpoints, but limits the tool descriptions must tell the model about):

- **`get_movers` only compares the latest day with the day before.** It cannot answer "moved most *this year*". For that the model must call `get_pair_trend` per currency. The description will say so.
- **There is no conversion or cross-rate endpoint.** Only EUR is stored as base. "€500 in naira" = `500 × latest EUR→NGN rate` (allowed arithmetic, rate shown). "USD→GBP" is **not** available; the agent must say so instead of dividing two rates itself.
- **Long series are summarised.** `get_pair_trend` returns a lot (series, stats, trend); the tool executor passes only the useful fields to the model.
- **`list_rates` is paginated** (cursor). The tool does not expose `cursor` in week 3: the model asks for a narrow date range or small `limit` instead. If more data existed the result says "more results exist; narrow the dates".

## 5. Behaviour rules (what the system prompt will say)

1. **Only tool results are facts.** Every number in an answer must come from a tool result in this conversation.
2. **Cite a source for every number**: endpoint + params, e.g. `GET /v1/pairs/EUR/NGN?window=3y`.
3. **Never compute long-range statistics in its head.** Use the fields the API returns (`change`, annualised change, volatility, moving averages…).
4. **Simple arithmetic is fine** (amount × rate) but **show the rate used** and its date.
5. **No predictions, no financial advice.** Refuse politely and offer what the data *can* show.
6. When discussing trends, say: **"past trends don't predict future rates."**
7. **Say clearly when data is missing**: a currency we don't store, a date before a currency's first stored date (data starts 2016-01-01 in our database).
8. **Ask before any write.** Never claim an alert was created unless the app confirms it.
9. **Answer in the user's language.**
10. **Keep answers short.**

These are reference rates, not tradable bank quotes. The agent must not imply otherwise.

## 6. Confirmation design (the only write)

The model can *request* `create_alert`, but the loop **never runs it**. Instead:

```
model: tool_use create_alert(EUR, USD, above, 1.20)
   │
   ▼
loop: does NOT call POST /v1/alerts.
      stores a PendingAction {id: <uuid>, tool: create_alert, input: {...}, status: pending}
      returns to the model:  "Not executed. Ask the user to confirm."
   │
   ▼
model: "I can create this alert: EUR→USD above 1.20. Please confirm."
   │
   ▼
app shows a card:  [ Confirm ]  [ Cancel ]
   │
   ├─ Confirm → app calls POST /v1/alerts with  Idempotency-Key = pending action id
   └─ Cancel  → action marked cancelled, nothing is created
```

Why this is safe:

- **The model cannot bypass it.** There is simply no code path from the model's request to the real POST. The only caller is the confirm button, which a human presses.
- **Double-click / retry safe.** The pending action id is used as the `Idempotency-Key` ([ADR 0006](decisions/0006-idempotent-alert-creation.md)). Clicking Confirm twice, or retrying after a timeout, returns the same alert instead of creating two.
- **Status guards.** A confirmed or cancelled action cannot be confirmed again with different effect (409 in the web endpoint, Phase 6).
- **Evals check it**: any alert created without a confirm step is an automatic fail.

## 7. What gets recorded per step (the transcript)

One **step** = one model call plus any tools it asked for. Recorded for debugging, the UI's "How I got this" list, and the evals:

| Field | Meaning |
|---|---|
| `step` | 1, 2, 3… |
| `model_text` | Any text the model wrote in that turn |
| `tool` / `input` | Which tool and its arguments (if any) |
| `request` | The real API request, e.g. `GET /v1/movers?base=EUR&limit=5` |
| `status` | HTTP status of that request (or `pending` for `create_alert`) |
| `result_summary` | Short version of what came back (not the full payload) |
| `tokens` | Input and output tokens for that model call |
| `duration_ms` | Time for the model call and the tool call |

A whole run also records: sources used, final answer, total tokens, estimated cost, and the pending action (if any). The API key is never recorded.

## 8. Settings (in `.env`)

`ANTHROPIC_API_KEY` (secret, never committed or logged) · `AGENT_MODEL` (`claude-sonnet-5-5`) · `AGENT_DEV_MODEL` (`claude-haiku-5-5`, cheap manual testing) · `AGENT_MAX_STEPS` (8) · `AGENT_API_BASE_URL` (`http://localhost:8000`).

## 9. Known limits (to be honest about in the UI and README)

Reference rates only · no news, so it can *describe* movements but not *explain why* they happened · no predictions · only EUR-based pairs · history starts 2016-01-01 in our database (unless extended).
