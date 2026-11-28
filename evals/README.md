# Agent evals

How we know whether the "Ask your data" agent is any good, and whether a change helped.

| File | What it is |
|---|---|
| `questions.yaml` | 18 questions in 7 categories, each with its judging rules. Written before the agent existed. |
| `build_expected.py` | Computes the correct answers from the **database with SQL** into `expected.json`. Never edit `expected.json` by hand. |
| `scoring.py` | The scorer: pure functions, unit-tested in `tests/evals/`. |
| `run.py` | Runs every question through the real agent and writes a report. |
| `results/` | Dated reports: `<date>-<model>[-label].md`. Committed, so scores can be compared over time. |

## Running it

```bash
uv run uvicorn fx_explorer.api.app:app --reload            # the agent's tools call this API
uv run python evals/run.py --label baseline                # the real model (AGENT_MODEL)
uv run python evals/run.py --only trend-01,write-01        # a few questions (report says "partial")
uv run python evals/run.py --dev                           # the cheaper AGENT_DEV_MODEL
```

`run.py` rebuilds `expected.json` first (latest rates move), then asks each question in a **fresh
conversation**. Real models cost money (free OpenRouter models cost $0 but are rate-limited).

## What "pass" means

A question passes only if **every** check passes: required numbers (within a tolerance, and the right
direction for changes), required dates, required behaviours (cites a source, refuses to predict,
asks for confirmation, says data is missing…) and no forbidden behaviours (invented numbers, a
prediction, advice, claiming an alert exists, a write without confirmation).

**Writes are never confirmed.** The eval never calls the confirm endpoint, and it compares the API's
alerts before and after every question: any new alert is an automatic fail.

**Invented numbers:** every number in an answer must appear in a tool result, in the question, or be
simple arithmetic on those (amount × rate, a sum or difference). Whole numbers up to 366 are ignored
("3 years") unless they carry a `%`.

## Honest limits of the scorer

It uses regular expressions, not an LLM judge: cheap and repeatable, but not perfect.

- A correct answer worded in an unusual way can fail a language check (a false failure). Read the
  failure's answer in the report before blaming the agent; if the check is wrong, **fix the check**
  (and add a test), not the expected answers.
- "Names the right currency" looks at which currency comes first in the first sentence.
- The step-by-step quality of the agent's reasoning is not judged, only the result.

## Improving the agent (the rules)

Change **only the system prompt and the tool descriptions** (`agent/prompts.py`, `agent/tools.py`).
Never change `questions.yaml` rules or `expected.json` to make a score go up. Run again with a new
`--label`, compare reports (the fingerprint line shows whether the prompt changed), and stop after at
most two rounds.
