# 0008. A tool-using analyst agent: a hand-written loop over our own API

- **Status:** Accepted
- **Date:** 2026-11-28

## Context

We want users to ask questions in plain English ("How has EUR→NGN changed over 3 years?") and get answers grounded in our stored rates. Which endpoints to call depends on the question, and one question may need several calls. We also want one safe write (creating a rate alert) without letting a language model change data on its own.

Constraints: every number must be traceable to our data; the author is learning, so the mechanism must be readable; tests must not cost money or need network; the API key must stay secret.

## Decision

1. **A tool-using agent with a hand-written loop** (in `src/fx_explorer/agent/loop.py`) on top of the official `anthropic` Python SDK and its Messages API with tool use. No agent framework. The loop: send question + tools → run any requested tools → append results → repeat until the model answers or `AGENT_MAX_STEPS` is reached.
2. **Tools are our own API endpoints, called over HTTP** (httpx), never the database. Each tool maps to one existing endpoint in `openapi.yaml`; a test checks the tool input schemas against the contract so they cannot drift. Problem Details errors become short readable tool results so the model can recover.
3. **The only write tool (`create_alert`) is never executed by the loop.** It creates a *pending action* with an id. The app shows Confirm / Cancel; on Confirm it calls `POST /v1/alerts` with `Idempotency-Key = pending action id` (ADR 0006), so retries and double-clicks cannot create two alerts.
4. **Behaviour is constrained by the system prompt and checked by an eval set** written before the agent (`evals/`), with ground truth computed from the database by SQL.
5. **Every step is recorded** (model text, tool, input, request, status, result summary, tokens, duration) in a transcript.
6. **Model access sits behind a small interface** with a real implementation and a scripted `FakeModel`, so tests are free and deterministic.

## Alternatives considered

- **Direct database access for the agent** (e.g. let it write SQL). Rejected: it would bypass the contract, validation, trend maths and idempotency we built; it could produce wrong or unsafe SQL; the answers would not match what the web app shows. Going through the API means one source of truth.
- **An agent framework** (LangChain, LlamaIndex, SDK agent runners…). Rejected for now: it hides the loop that we want to learn and control, adds dependencies, and makes the confirmation interception and per-step recording harder to see. The loop is about 50 lines; writing it is the lesson.
- **A fixed workflow** (classify the question, then run a hard-coded sequence). Rejected: the number of cases grows with every new kind of question, and it cannot handle multi-step questions ("which currency moved most this year?") unless we code each one. Kept as the right tool for steady jobs such as a daily briefing.
- **Letting the model call `create_alert` directly, with a "please confirm" instruction in the prompt.** Rejected: a prompt is a request, not a guarantee. Safety for writes must be enforced in code.

## Consequences

- **Easier:** answers always match the web app and API; one place (the contract) defines what data exists; tests are free; the confirmation rule cannot be bypassed by a clever prompt.
- **Harder / costs:** each question costs tokens (several model calls); behaviour is less predictable than a workflow, so we need the eval set and must re-run it after prompt changes; every new API capability needs a tool description written for the model.
- **Limits we accept:** the agent can only answer what our endpoints expose. There is no cross-rate or conversion endpoint, and `/v1/movers` only covers the latest day, so some questions need several calls or an honest "I can't".
- **Remember:** the model's text is never a source of truth. Numbers come from tool results, and the system prompt, the tool descriptions and the evals exist to keep it that way.

## Update 2026-11-28: a second model provider (OpenRouter)

The model sits behind the small `Model` interface (decision 6), so the provider can change without touching the loop or the tools. `AGENT_PROVIDER` selects `anthropic` (the official SDK, the default) or `openrouter` (OpenRouter's OpenAI-style chat-completions API, called with `httpx`, no new dependency). `OpenRouterModel` only translates our Anthropic-style messages to the OpenAI format and back, with the same retry rules. Consequences: free OpenRouter models can be used for development and evals at no cost, but they may be slower, rate-limited and weaker at tool use than Claude models, so eval scores are only comparable within one model. Prompts and questions are sent to OpenRouter and the model's own provider.
