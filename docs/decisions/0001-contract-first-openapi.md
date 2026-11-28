# 0001. Contract-first API with OpenAPI 3.1

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

Week 2 adds two new parts: a REST API and a web UI that talks to it. They need to agree on every URL, parameter, field name, type and error. If we let the server code define the API, the UI ends up built against whatever the code happens to do, including its bugs. The UI also has to wait until the server exists.

FastAPI can generate an OpenAPI document from Python code ("code-first"). That's convenient, but the document then just describes whatever the code does. Nobody reviews the API design before it's built, and small accidents (a field that is sometimes missing, a 422 where we wanted 400) quietly become part of the API.

## Decision

We will write the API contract **first**, by hand, in `openapi.yaml` (OpenAPI **3.1**), and treat it as the single source of truth.

- **Order of work:** write or change `openapi.yaml` → lint it → (if a rule changes) write a decision record → implement.
- **Linting:** `npx @redocly/cli lint openapi.yaml` using Redocly's `recommended` ruleset (configured in `redocly.yaml`). It must report 0 errors.
- **One contract only:** the server serves *our* file at `/openapi.yaml` and points Swagger UI at it. FastAPI's auto-generated spec is turned off, so there are never two competing descriptions.
- **Contract tests:** pytest tests with FastAPI's `TestClient`, plus **schemathesis**, which reads `openapi.yaml`, generates many valid and invalid requests, and checks that every response (status code, headers, body) matches the contract.
- **The UI is built from the contract only:** TypeScript types are generated from `openapi.yaml` with `openapi-typescript`, and all calls go through the typed `openapi-fetch` client. UI work never looks at server code.
- **Who wins on disagreement:** the contract. If the code and contract disagree, the code is wrong, unless we deliberately change the contract first.

Why **3.1** rather than 3.0: 3.1 schemas *are* standard JSON Schema (2020-12), so things like `type: [string, "null"]`, `const` and `examples` work as in any JSON Schema tool. 3.0 used its own dialect (`nullable: true`) with known quirks. Redocly, schemathesis, openapi-typescript and FastAPI's Swagger UI all support 3.1.

## Alternatives considered

- **Code-first (FastAPI generates the spec):** the fastest to start with and always "in sync". But the spec only describes what the code does, so it can't catch the code being wrong. Design review happens after the build, and FastAPI's defaults (e.g. 422 for bad query parameters, its own error shape) leak into the API. Rejected.
- **OpenAPI 3.0:** wider support in older tools, but the non-standard schema dialect makes nullable fields and examples awkward. Every tool we need supports 3.1. Rejected.
- **No formal contract (README table + tests):** less up-front work, but we couldn't generate UI types or run property-based contract tests. Rejected.

## Consequences

- ✅ The UI and the API can be built independently, and either can be checked against the contract.
- ✅ Schemathesis finds edge cases we wouldn't think to write tests for (empty strings, huge numbers, odd dates).
- ✅ The generated TypeScript types make a renamed field a compile error in the UI, not a blank screen.
- ⚠️ We maintain the contract **and** the pydantic models by hand, so they can drift. Contract tests are the safety net, and they must run on every change.
- ⚠️ Writing YAML up front feels slower at first. It pays off when the UI phase starts.
- ⚠️ We must remember to disable FastAPI's own `/openapi.json` and `/docs` generation, and serve ours instead.
