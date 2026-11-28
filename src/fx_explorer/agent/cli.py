"""Terminal commands for the agent:

    uv run python -m fx_explorer ask "How has EUR to NGN moved over the last 3 years?"
    uv run python -m fx_explorer chat

    --verbose   print every step (model text, tool, request, result summary, tokens)
    --dev       use AGENT_DEV_MODEL (cheaper) instead of AGENT_MODEL

Every run is saved as JSON in logs/agent/ (gitignored).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from fx_explorer.agent.loop import Agent, AgentResult
from fx_explorer.agent.model import AnthropicModel, Model, ModelError, OpenRouterModel
from fx_explorer.agent.tools import PendingAction, ToolBox
from fx_explorer.config import Settings, get_settings

LOG_DIR = Path("logs/agent")
EXIT_WORDS = {"exit", "quit", "q", ":q"}


def add_parsers(sub: argparse._SubParsersAction) -> None:
    ask = sub.add_parser("ask", help="ask the analyst agent one question")
    ask.add_argument("question")
    chat = sub.add_parser("chat", help="talk to the analyst agent (several questions in a row)")
    for p in (ask, chat):
        p.add_argument("--verbose", action="store_true", help="print every step the agent takes")
        p.add_argument("--dev", action="store_true", help="use AGENT_DEV_MODEL (cheaper)")


def run(
    args: argparse.Namespace,
    *,
    settings: Settings | None = None,
    model: Model | None = None,
    input_fn: Callable[[str], str] = input,
    out: Callable[..., None] = print,
    interactive: bool | None = None,
    log_dir: Path = LOG_DIR,
) -> int:
    """Entry point for `ask` and `chat`. `model`, `input_fn` and `out` can be replaced in tests."""
    settings = settings or get_settings()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")

    if model is None:
        try:
            model = _real_model(settings, dev=args.dev)
        except ModelError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    toolbox = ToolBox(settings.agent_api_base_url, settings.http_timeout_seconds)
    try:
        if not _api_is_up(settings.agent_api_base_url):
            print(
                f"Error: can't reach the API at {settings.agent_api_base_url}. Start it with:\n"
                "  uv run uvicorn fx_explorer.api.app:app --reload",
                file=sys.stderr,
            )
            return 2
        agent = Agent(model, toolbox, max_steps=settings.agent_max_steps)
        ask_to_confirm = sys.stdin.isatty() if interactive is None else interactive
        session = _Session(agent, toolbox, args.verbose, input_fn, out, log_dir, ask_to_confirm)
        out(f"Model: {model.name}  (type 'exit' to leave)" if args.command == "chat" else f"Model: {model.name}")
        if args.command == "ask":
            return 1 if session.turn(args.question).status == "error" else 0
        return session.chat_loop()
    finally:
        toolbox.close()


def _real_model(settings: Settings, dev: bool) -> Model:
    name = settings.agent_dev_model if dev else settings.agent_model
    if settings.agent_provider == "openrouter":
        key = settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else None
        return OpenRouterModel(
            model=name, api_key=key, base_url=settings.openrouter_base_url,
            max_retries=settings.max_retries,
        )
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    return AnthropicModel(model=name, api_key=key, max_retries=settings.max_retries)


def _api_is_up(base_url: str) -> bool:
    try:
        return httpx.get(f"{base_url.rstrip('/')}/v1/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


class _Session:
    """One terminal conversation: runs turns, prints them, handles Confirm / Cancel."""

    def __init__(self, agent, toolbox, verbose, input_fn, out, log_dir, ask_to_confirm) -> None:
        self.agent: Agent = agent
        self.toolbox: ToolBox = toolbox
        self.verbose: bool = verbose
        self.input_fn = input_fn
        self.out = out
        self.log_dir: Path = log_dir
        self.ask_to_confirm: bool = ask_to_confirm
        self.history: list[dict[str, Any]] = []
        self.notices: list[str] = []  # app events the model hasn't been told about yet

    def chat_loop(self) -> int:
        while True:
            try:
                question = self.input_fn("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                self.out()
                return 0
            if not question:
                continue
            if question.lower() in EXIT_WORDS:
                return 0
            self.turn(question)

    def turn(self, question: str) -> AgentResult:
        # The app (not the model) creates alerts. So the model learns about a Confirm/Cancel
        # from a note in front of the user's next message.
        prefix = "".join(f"[App notice: {n}]\n" for n in self.notices)
        self.notices.clear()
        result = self.agent.run(prefix + question, history=self.history)
        self.history = result.messages
        if self.verbose:
            self._print_steps(result)
        self._print_result(result)
        path = result.transcript.save(self.log_dir, result.to_dict())
        self.out(f"(transcript: {path})")
        if result.pending_action is not None:
            self._confirm_flow(result.pending_action)
        return result

    # -- confirmation
    def _confirm_flow(self, action: PendingAction) -> None:
        a = action.input
        self.out(
            f"\nPending alert {action.id[:8]}: {a['base']}/{a['quote']} {a['condition']} {a['threshold']}"
            + (f' (note: "{a["note"]}")' if a.get("note") else "")
        )
        if not self.ask_to_confirm:
            self.out("Not created. Run `chat` to confirm it interactively.")
            return
        try:
            answer = self.input_fn("Confirm? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            self.toolbox.cancel(action)
            self.out("Cancelled. No alert was created.")
            self.notices.append(f"the user cancelled the proposed alert {action.id}; nothing was created")
            return
        result = self.toolbox.confirm(action)
        if result.is_error:
            self.out(f"Could not create the alert: {result.content}")
            self.notices.append(f"confirming alert {action.id} failed: {result.content}")
            return
        alert = json.loads(result.content)
        again = " (this exact alert already existed; no duplicate was made)" if result.replayed else ""
        self.out(f"Alert created: {alert['pair']} {alert['condition']} {alert['threshold']}, id {alert['id']}{again}")
        self.notices.append(
            f"the user confirmed and the alert was created: {alert['pair']} {alert['condition']} "
            f"{alert['threshold']}, id {alert['id']}"
        )

    # -- printing
    def _print_steps(self, result: AgentResult) -> None:
        for s in result.steps:
            self.out(f"\n── step {s.step}  ({s.input_tokens} in / {s.output_tokens} out tokens, {s.model_duration_ms} ms)")
            if s.model_text:
                self.out(f"   model: {s.model_text}")
            for c in s.tool_calls:
                self.out(f"   tool:    {c.tool}({json.dumps(c.input)})")
                self.out(f"   request: {c.request or '(no HTTP request)'}  -> {c.status}{' ERROR' if c.is_error else ''}")
                self.out(f"   result:  {c.result_summary}")

    def _print_result(self, result: AgentResult) -> None:
        self.out(f"\n{result.answer}")
        if result.sources:
            self.out("\nsources:")
            for s in result.sources:
                self.out(f"  {s}")
        u = result.usage
        cost = "n/a" if result.cost_usd is None else f"${result.cost_usd:.4f}"
        self.out(
            f"\n[{result.status}] {len(result.steps)} steps, {u.input_tokens} in / {u.output_tokens} out tokens"
            f" (cached read {u.cache_read_tokens}, write {u.cache_write_tokens}), "
            f"est. cost {cost}, {result.duration_ms / 1000:.1f}s"
        )
