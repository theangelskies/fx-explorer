# 0002. Errors use RFC 9457 Problem Details

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

Every API needs a consistent error shape, or every client ends up writing special cases. Without a decision we would get a mix: FastAPI's validation errors (`{"detail": [{"loc": [...], "msg": ..., "type": ...}]}`), its 404s (`{"detail": "Not Found"}`), plain-text 500s, and whatever our own code raises.

The UI needs two things from errors:

1. Something to show in a general error state (a title and a sentence).
2. For forms, a message per field to show under the matching input.

There is a published standard for (1): **RFC 9457, Problem Details for HTTP APIs** (it replaced RFC 7807 in 2023). It doesn't define (2), but explicitly allows extension members.

## Decision

Every error response, without exception, has `Content-Type: application/problem+json` and this body:

| Member | Type | Meaning |
|---|---|---|
| `type` | string (URI reference) | Identifies the kind of problem. Clients branch on this. |
| `title` | string | Short, human-readable summary. Same for every problem of this `type`. |
| `status` | integer | The HTTP status code (repeated for convenience in logs). |
| `detail` | string | Explanation specific to this occurrence. |
| `instance` | string | The request path that produced the problem, e.g. `/v1/alerts`. |
| `errors` | array of `{field, message}` | **Extension.** Only on validation problems (400, 422). One entry per invalid field. |

All members except `errors` are always present.

**Problem types** (relative URI references, documented in `openapi.yaml`):

| `type` | Status | When |
|---|---|---|
| `/problems/invalid-parameters` | 400 | A query or path parameter is invalid (bad date, `from` > `to`, unknown currency in a filter, `limit` out of range, bad cursor). Has `errors`. |
| `/problems/not-found` | 404 | Unknown resource, pair without data, or unknown route. |
| `/problems/method-not-allowed` | 405 | Wrong HTTP method for an existing path. |
| `/problems/idempotency-key-in-use` | 409 | An earlier request with the same `Idempotency-Key` is still being processed. |
| `/problems/validation-error` | 422 | The request body breaks a rule. Has `errors`. |
| `/problems/idempotency-key-reused` | 422 | An `Idempotency-Key` was reused with a different request body. |
| `/problems/internal-error` | 500 | Unexpected failure. `detail` is generic, and nothing internal (stack trace, SQL) is exposed. |
| `/problems/service-unavailable` | 503 | The database can't be reached. |

**400 vs 422.** Invalid query/path parameters → **400**. Invalid request body → **422**.

- 400 means "the request (URL) is malformed". It is usually a client-code bug, and a developer fixes it.
- 422 means "the JSON is well-formed, but the values aren't acceptable". This is usually user input, fixed by editing a form. RFC 9110 defines 422 exactly this way.

This makes the UI simple: a 422 with `errors` → show each `message` under the input named by `field`.

**`field` naming:** the parameter or JSON property name exactly as in the contract (`threshold`, `from`, `limit`). For a rule involving two fields (e.g. `base` = `quote`) we report it on the field the user should change (`quote`). A malformed (unparseable) body has `field: "body"`.

**Framework errors are converted too:** FastAPI/Starlette's own validation errors, unknown-route 404s, 405s and any unhandled exception all go through our handlers in `api/errors.py`.

## Alternatives considered

- **FastAPI's default error format:** zero work, but there's no standard behind it, it has a different shape for 404 vs 422, and the `loc`/`msg`/`type` fields are framework internals. Rejected.
- **A custom format, e.g. `{"error": {"code": ..., "message": ...}}`:** just as easy to implement, but it's our own invention with no standard behind it. Problem Details is supported by many tools and recognised by other developers. Rejected.
- **422 for every validation error (FastAPI's default):** simpler on the server, but it erases the useful difference between "the developer built a bad URL" and "the user typed a bad value". Rejected.
- **`type: "about:blank"` everywhere:** allowed by the RFC (it then means "just the HTTP status"), but clients couldn't tell "idempotency key reused" from "validation error", which are both 422. Rejected; we use specific types.
- **Absolute `type` URIs (`https://…/problems/…`):** the RFC recommends absolute URIs, but we have no public domain to host documentation on. Relative references are permitted. If the API is ever hosted, we can switch without changing any client logic, since clients compare the path. Revisit then.

## Consequences

- ✅ One error shape for the UI's `<ErrorState problem={…}/>` component and one form-error rendering rule.
- ✅ Clients can branch on a stable `type` instead of parsing English text.
- ⚠️ We must override FastAPI's built-in handlers (`RequestValidationError`, `HTTPException`, `Exception`) and map pydantic error locations to our `field` names. That code needs its own tests.
- ⚠️ FastAPI validates query parameters and the body with the same machinery and reports both as one error type. Our handler must tell them apart (by the error location: `query`/`path` vs `body`) to choose 400 or 422.
- ⚠️ Error messages are part of the user experience now, so they must be written for people, not copied from pydantic.
