# 0006. Idempotent alert creation with an Idempotency-Key

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

`POST /v1/alerts` creates a new alert every time it's called. That's what POST means. But a
client can't always tell whether its request worked:

- the response is lost on the way back (timeout, Wi-Fi drop, phone goes to sleep), so the
  client retries a request the server already processed;
- HTTP libraries and proxies retry automatically on timeouts;
- a user double-clicks "Create alert" before the first click has an answer.

Each of these creates a **duplicate alert**. GET, PUT and DELETE don't have this problem, because
repeating them leaves the server in the same state (they're idempotent by definition in RFC 9110).
POST isn't, so we need a way for the client to say "this is a retry of the same action".

## Decision

We implement the **Idempotency-Key** pattern, as popularised by Stripe and specified in the IETF
draft [*The Idempotency-Key HTTP Header Field*](https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/).

The client may send `Idempotency-Key: <unique value>` (max 255 characters; a UUID is ideal),
generated once per *intended* action and reused only for retries of that action.

| Situation | Response |
|---|---|
| No key | Normal behaviour: every POST creates an alert. |
| New key | Claim it as `in_progress`, validate, create the alert, store `201` + body, mark `completed`. |
| Same key, same body, completed | The **stored** response: same status, same body (same `id`), same `Location`, plus `Idempotent-Replayed: true`. No new alert. |
| Same key, **different** body | `422` `/problems/idempotency-key-reused`. A key was reused for another action. |
| Same key, first request still running | `409` `/problems/idempotency-key-in-use`. Retry shortly. |

Details:

- **"Same body"** = equal SHA-256 hash of the body as canonical JSON (keys sorted, no
  whitespace). Key order and formatting don't matter; every value does (including `"note": null`
  vs. leaving `note` out).
- **The body is compared before the status.** A different body gets 422 even while the first
  request is running, because it's a client bug either way.
- **Atomic claim.** `INSERT … ON CONFLICT` on the key's primary key guarantees that of two
  simultaneous requests exactly one proceeds; the other gets 409. (Tested with two real threads.)
- **One transaction.** The new alert and the stored response are written in the *same* database
  transaction. A crash can never leave an alert without its stored response, which would let a
  retry create a second alert.
- **Only requests that ran are stored** (as Stripe does). If validation fails (422) or the server
  errors (500/503), the key is released, so the client can fix the body or simply retry with
  the same key.
- **Stuck keys.** If the server dies between claiming and finishing, the key would say
  `in_progress` forever. An `in_progress` key older than **60 seconds** can be taken over by the
  next request with that key. Completed keys are never taken over.
- **A replay is the stored response, not a fresh read.** `current_rate` and `triggered` in a
  replay are what the first request returned. Use `GET /v1/alerts/{id}` for the live values.
- **A replay after a delete still replays.** If the alert was created with key K and later
  deleted, a retry with K gets the stored `201`, whose `Location` now returns `404`. The retry is
  a retry of a creation that *did* happen, so creating a second alert would be wrong (it would
  bring back an alert the user removed). This matches Stripe and the IETF draft, and is stated in
  the contract. Schemathesis' stateful check "a created resource must be retrievable"
  (ST302, `ensure_resource_availability`) assumes otherwise, so that one check is turned off for
  `createAlert` only, in `schemathesis.toml`. Found by schemathesis, decided on 2026-11-26.

## Alternatives considered

- **Do nothing; let clients avoid retries:** clients can't know whether to retry, and the
  common cases (timeouts, double-clicks) are exactly the ones they can't avoid. Rejected.
- **Detect duplicates by content** (refuse a second identical alert): a user may *want* two
  identical alerts, and it can't tell a retry from an intentional repeat. Rejected.
- **Client-generated ids with `PUT /v1/alerts/{id}`:** idempotent by HTTP semantics, but a bigger
  contract change, and the server no longer controls ids. Reasonable, but the Idempotency-Key
  header is the widely used convention for "create" APIs. Rejected for now.
- **Forget the key when its alert is deleted:** schemathesis' stateful check would pass, but a
  delayed retry arriving after a delete would silently re-create the alert the user removed.
  Rejected.
- **Store validation errors too:** then a user who fixes a typo and retries with the same key
  would get "key reused with a different body". Following Stripe, only executed requests are
  stored. Rejected.
- **Keep keys in memory instead of PostgreSQL:** faster, but lost on restart and not shared
  between server processes, so it wouldn't survive exactly the failures it's meant for. Rejected.

## Consequences

- ✅ Retries and double-clicks are safe: a client can always resend with the same key.
- ✅ Clients can tell a replay from a fresh creation (`Idempotent-Replayed: true`).
- ⚠️ Keys are kept forever for now. Stripe expires them after 24 hours; a scheduled
  `DELETE FROM idempotency_keys WHERE created_at < now() - interval '1 day'` would do the same.
  Not needed at our size, but noted.
- ⚠️ Keys are global (there are no user accounts). With accounts, a key would have to be unique
  per user, otherwise two users could collide on the same key.
- ⚠️ A replay can show an outdated `current_rate`/`triggered`. That's the point of a replay, but
  the UI should re-read the list after creating, not trust the POST response forever.
- ⚠️ The 60-second takeover assumes no request legitimately runs longer than that. Alert creation
  takes milliseconds.
