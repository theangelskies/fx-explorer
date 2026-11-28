# 0009. The agent endpoint: conversations and pending actions in PostgreSQL

- **Status:** Accepted
- **Date:** 2026-11-28

## Context

The web app needs three things from the agent ([ADR 0008](0008-tool-using-agent.md)): ask a question (and follow up), and confirm or cancel an alert the agent proposed. That needs **state on the server**:

- A follow-up ("and over 5 years?") only makes sense with the earlier messages.
- A **pending action** (a proposed alert) must be remembered until a person clicks Confirm or Cancel, possibly minutes later, possibly after the API restarted. The browser must send only the action's id, never the alert's details: otherwise a client could make the server create an alert the agent never proposed.

Where should this state live: in memory, in the browser, or in the database?

## Decision

1. **Two small tables** (migration `003`):
   - `agent_conversations(id, messages jsonb, version, created_at, updated_at)`: the full message history the agent needs to continue.
   - `agent_actions(id, conversation_id, tool, input jsonb, status, alert_id, notified, created_at, updated_at)`: proposed writes. `status` is `pending | confirmed | cancelled`. The action `id` is the id the agent created, and doubles as the `Idempotency-Key`.
   SQL lives in a repository, like every other table.
2. **The agent runs inside the API process**, in a normal (threadpool) route. Its tools still call this API **over HTTP** at `AGENT_API_BASE_URL` (golden rule: never the database directly).
3. **Confirm reuses the existing alert creation**, not a second implementation: the same function that serves `POST /v1/alerts` is called with the action's stored input and `Idempotency-Key = action id`. Validation, the single transaction and the idempotency table are shared (ADR 0006).
4. **State rules** (the one place we refine the original sketch "409 when already confirmed or cancelled"):
   - confirm a **pending** action: create the alert, `201`;
   - confirm an **already confirmed** action: `201` again with the *same* alert and `Idempotent-Replayed: true`. A retry after a lost response must not turn into an error;
   - confirm a **cancelled** action: `409`;
   - cancel a **pending or cancelled** action: `204` (cancelling twice is fine);
   - cancel a **confirmed** action: `409` (the alert exists; delete it via `DELETE /v1/alerts/{id}`).
5. **Concurrency:** a conversation row has a `version`. A question loads the conversation, runs the agent (seconds), and saves with `WHERE version = <loaded>`. If another request saved in between, the save fails and the answer is `409` ("ask again"). No row lock is held while the model runs.
6. **The model learns about Confirm/Cancel** the same way the CLI does: the next question in that conversation gets an `[App notice: …]` prefix for each action decided since the last question (tracked by `notified`).
7. **Limits:** a question is 1–1000 characters with at least one non-space character; a conversation holds at most 20 questions (it is resent to the model every time, so cost grows with length).
8. **Errors** are Problem Details: `422` bad question, `404` unknown conversation or action, `409` conflicts, `503` when the language model fails after its retries (a new generic `conflict` problem type is added for 409).
9. **Tests never call a real model:** with `AGENT_FAKE=1` the API builds the agent on a rule-based fake model (and a fixed currency list), so schemathesis and the endpoint tests are free and deterministic.

## Alternatives considered

- **In memory (a dict in the API process).** The least code. Rejected: a restart, or uvicorn `--reload` during development, forgets every pending action (the user clicks Confirm and gets 404), and it breaks with more than one worker process.
- **The browser keeps the history and sends it each time (stateless API).** Rejected: the client could forge earlier "tool results" or pending actions, which breaks the rule that every number comes from a real tool result and every write from a real proposal.
- **A cache server such as Redis.** Rejected: new infrastructure for a small amount of data, and PostgreSQL is already there and already used for idempotency keys.
- **Strict 409 on a second confirm.** Rejected: the point of using the action id as an idempotency key is that retries are safe. Returning the stored alert is the same behaviour `POST /v1/alerts` already has.

## Consequences

- **Easier:** pending actions survive restarts; confirm is idempotent end to end; the web app only ever sends ids and questions.
- **Harder / costs:** a migration and two repositories; the history JSON (which includes the model's own turns) is stored in the database, so it holds users' questions and should get a retention rule; conversations and actions are never deleted yet (Roadmap: expire old ones).
- **Remember:** `agent_conversations.messages` must stay append-only; never edit earlier entries (the model's earlier turns must be handed back unchanged).
- **Remember:** the agent calls this same API over HTTP, so the API must be reachable at `AGENT_API_BASE_URL` (default `http://localhost:8000`). If it is not, the agent's tools report that, and the answer says it could not read the data.
