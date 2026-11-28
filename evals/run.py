"""Run the agent on every question in evals/questions.yaml and score it.

    uv run python evals/run.py                      # the real model (AGENT_MODEL)
    uv run python evals/run.py --label round1       # tag the report: 2026-11-28-<model>-round1.md
    uv run python evals/run.py --only trend-01,write-01
    uv run python evals/run.py --dev                # the cheaper AGENT_DEV_MODEL

What happens, per question:
  1. A FRESH conversation (no history carries over).
  2. The agent runs for real: it calls the API over HTTP, which must be running.
  3. Writes are NEVER confirmed. The agent can only propose an alert; this script never calls
     confirm. It also compares the API's alerts before and after: a new alert = automatic fail.
  4. The answer is scored against expected.json (computed from the database) by scoring.py.

It writes evals/results/<date>-<model>[-label].md (committed) and the raw answers and checks to
logs/evals/ (not committed). Cost: real-model runs spend money (free OpenRouter models cost $0).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import build_expected  # noqa: E402
from evals.scoring import RunRecord, Score, score_question  # noqa: E402
from fx_explorer.agent.factory import build_agent  # noqa: E402
from fx_explorer.agent.loop import Agent, AgentResult  # noqa: E402
from fx_explorer.agent.model import ModelError  # noqa: E402
from fx_explorer.agent.prompts import SYSTEM_PROMPT  # noqa: E402
from fx_explorer.agent.tools import TOOL_DEFINITIONS  # noqa: E402
from fx_explorer.config import get_settings  # noqa: E402

HERE = Path(__file__).parent


def prompt_fingerprint() -> str:
    """Short hash of the system prompt and tool descriptions: shows whether two runs used the same ones."""
    material = SYSTEM_PROMPT + json.dumps([(t["name"], t["description"], t["input_schema"]) for t in TOOL_DEFINITIONS], sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:8]


def alert_ids(client: httpx.Client) -> set[str]:
    ids: set[str] = set()
    cursor = None
    for _ in range(10):
        params: dict[str, Any] = {"limit": 200, **({"cursor": cursor} if cursor else {})}
        body = client.get("/v1/alerts", params=params).raise_for_status().json()
        ids |= {a["id"] for a in body["data"]}
        cursor = body["page"]["next_cursor"]
        if not cursor:
            break
    return ids


def to_record(result: AgentResult, new_alerts: set[str]) -> RunRecord:
    tool_texts = [
        str(block["content"])
        for message in result.messages
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if block["type"] == "tool_result"
    ]
    return RunRecord(
        answer=result.answer,
        status=result.status,
        error=result.error,
        pending_action=None if result.pending_action is None else dict(result.pending_action.input),
        tool_calls=[
            {"tool": c.tool, "request": c.request, "status": c.status, "is_error": c.is_error}
            for step in result.steps for c in step.tool_calls
        ],
        tool_texts=tool_texts,
        new_alert_ids=sorted(new_alerts),
        steps=len(result.steps),
        tokens=result.usage.input_tokens + result.usage.output_tokens + result.usage.cache_read_tokens,
        cost_usd=result.cost_usd,
        duration_s=result.duration_ms / 1000,
    )


def run_question(agent: Agent, client: httpx.Client, question: dict, expected: dict) -> tuple[RunRecord, Score]:
    before = alert_ids(client)
    result = agent.run(question["question"])  # no history: a fresh conversation every time
    new_alerts = alert_ids(client) - before
    record = to_record(result, new_alerts)
    return record, score_question(question, expected, record)


# ----------------------------------------------------------------------------- the report


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def build_report(rows: list[dict], meta: dict) -> str:
    total = len(rows)
    passed = sum(r["score"].passed for r in rows)
    cost_known = [r["run"].cost_usd for r in rows if r["run"].cost_usd is not None]
    total_cost = sum(cost_known) if cost_known else None
    tokens = sum(r["run"].tokens for r in rows)
    lines = [
        f"# Eval report: {meta['model']} ({meta['date']}{', ' + meta['label'] if meta['label'] else ''})",
        "",
        f"- **Overall score: {passed}/{total} ({100 * passed / total:.0f}%)**" + ("  ⚠ partial run (--only)" if meta["partial"] else ""),
        f"- **Total cost: {_money(total_cost)}** · {tokens:,} tokens · {sum(r['run'].duration_s for r in rows):.0f} s",
        f"- Provider: `{meta['provider']}` · model: `{meta['model']}` · max steps: {meta['max_steps']}",
        f"- Prompt + tool fingerprint: `{meta['fingerprint']}` (equal fingerprints = same prompt and tool descriptions)",
        f"- Ground truth: `evals/expected.json`, built from the database (newest stored rate date {meta['data_date']})",
        "- Writes are never confirmed by the eval; any alert created during a run is an automatic fail.",
        "",
        "## By category",
        "",
        "| Category | Passed | Total |",
        "|---|---|---|",
    ]
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        by_cat[r["q"]["category"]].append(r["score"].passed)
    for cat, results in by_cat.items():
        lines.append(f"| {cat} | {sum(results)} | {len(results)} |")
    lines += ["", "## Questions", "", "| id | category | result | reason | steps | tokens | cost |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        s, run = r["score"], r["run"]
        reason = "—" if s.passed else s.reasons[0].replace("|", "\\|")
        if not s.passed and len(s.reasons) > 1:
            reason += f" (+{len(s.reasons) - 1} more)"
        lines.append(
            f"| {r['q']['id']} | {r['q']['category']} | {'✅ pass' if s.passed else '❌ fail'} | {reason} | {run.steps} | {run.tokens:,} | {_money(run.cost_usd)} |"
        )
    failures = [r for r in rows if not r["score"].passed]
    lines += ["", "## Failures in detail", ""]
    if not failures:
        lines.append("None.")
    for r in failures:
        lines += [f"### {r['q']['id']}: {r['q']['question']}", ""]
        lines += [f"- ❌ {reason}" for reason in r["score"].reasons]
        lines += ["", "> " + (r["run"].answer.strip()[:700] or "(no answer)").replace("\n", "\n> "), ""]
    lines += ["## All answers", ""]
    for r in rows:
        lines += [
            f"<details><summary>{'✅' if r['score'].passed else '❌'} {r['q']['id']}: {r['q']['question']}</summary>",
            "",
            "> " + (r["run"].answer.strip() or "(no answer)").replace("\n", "\n> "),
            "",
            "Checks: " + "; ".join(f"{'✓' if ok else '✗'} {name}" for name, ok, _ in r["score"].checks),
            "",
            "</details>",
            "",
        ]
    return "\n".join(lines)


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    path = directory / f"{stem}{suffix}"
    n = 2
    while path.exists():
        path = directory / f"{stem}-{n}{suffix}"
        n += 1
    return path


# ----------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the agent eval.")
    parser.add_argument("--dev", action="store_true", help="use AGENT_DEV_MODEL")
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--label", default="", help="tag for the report file name, e.g. baseline, round1")
    parser.add_argument("--out-dir", type=Path, default=HERE / "results")
    parser.add_argument("--no-rebuild", action="store_true", help="don't recompute expected.json first")
    args = parser.parse_args(argv)

    settings = get_settings()
    questions = yaml.safe_load((HERE / "questions.yaml").read_text())
    if args.only:
        wanted = set(args.only.split(","))
        unknown = wanted - {q["id"] for q in questions}
        if unknown:
            parser.error(f"unknown question ids: {', '.join(sorted(unknown))}")
        questions = [q for q in questions if q["id"] in wanted]

    if not args.no_rebuild:
        build_expected.main(quiet=True)  # latest rates move: always score against fresh ground truth
    expected = json.loads((HERE / "expected.json").read_text())

    try:
        with httpx.Client(base_url=settings.agent_api_base_url, timeout=10) as probe:
            probe.get("/v1/health").raise_for_status()
    except httpx.HTTPError:
        print(f"Error: can't reach the API at {settings.agent_api_base_url}. Start it first:\n"
              "  uv run uvicorn fx_explorer.api.app:app --reload", file=sys.stderr)
        return 2
    try:
        agent = build_agent(settings, dev=args.dev)
    except ModelError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    print(f"Model {agent.model_name}: {len(questions)} questions\n")
    with httpx.Client(base_url=settings.agent_api_base_url, timeout=10) as client:
        try:
            for q in questions:
                started = time.monotonic()
                run, score = run_question(agent, client, q, expected[q["id"]])
                rows.append({"q": q, "run": run, "score": score})
                mark = "PASS" if score.passed else "FAIL"
                print(f"{mark} {q['id']:<14} {run.steps} steps {run.tokens:>6} tokens {time.monotonic() - started:5.1f}s"
                      + ("" if score.passed else f"  <- {score.reasons[0]}"))
        finally:
            agent.close()

    date = dt.date.today().isoformat()
    slug = re.sub(r"[^A-Za-z0-9.]+", "-", agent.model_name).strip("-")
    stem = f"{date}-{slug}" + (f"-{args.label}" if args.label else "")
    meta = {
        "date": date, "model": agent.model_name, "label": args.label, "provider": "fake" if settings.agent_fake else settings.agent_provider,
        "max_steps": settings.agent_max_steps, "fingerprint": prompt_fingerprint(),
        "data_date": expected["_meta"]["newest_stored_date"], "partial": bool(args.only),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report_path = unique_path(args.out_dir, stem, ".md")
    report_path.write_text(build_report(rows, meta) + "\n")

    raw_dir = ROOT / "logs" / "evals"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = [{"id": r["q"]["id"], "passed": r["score"].passed, "checks": r["score"].checks, "answer": r["run"].answer,
            "tool_calls": r["run"].tool_calls, "pending_action": r["run"].pending_action} for r in rows]
    unique_path(raw_dir, stem, ".json").write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")

    passed = sum(r["score"].passed for r in rows)
    costs = [r["run"].cost_usd for r in rows if r["run"].cost_usd is not None]
    print(f"\nScore {passed}/{len(rows)} · cost {_money(sum(costs) if costs else None)} · report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
