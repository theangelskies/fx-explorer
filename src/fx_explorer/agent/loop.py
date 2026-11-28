"""The agent loop: question -> model -> tools -> model -> ... -> answer.

The model never runs anything. It only ASKS for tools. This loop does the running, and
decides what is allowed. In pseudo-code:

    messages = [..., the user's question]
    repeat up to MAX_STEPS times:
        reply = model(system prompt, messages, tool menu)
        messages += reply
        if the reply asks for no tools:  -> that text is the answer, stop
        run each requested tool (create_alert is only RECORDED, never run)
        messages += the tool results
    (still asking for tools after MAX_STEPS? -> stop and say what we found)

`messages` only ever grows (append-only). We never edit or delete earlier entries: the
model's own earlier turns (including its hidden "thinking") must be handed back unchanged.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fx_explorer.agent.model import Model, ModelError, Usage, estimate_cost_usd
from fx_explorer.agent.prompts import CurrencyContext, build_system_prompt
from fx_explorer.agent.tools import TOOL_DEFINITIONS, PendingAction, ToolBox, ToolResult
from fx_explorer.agent.transcript import Step, ToolCallRecord, Transcript, summarize

logger = logging.getLogger(__name__)

STEP_LIMIT_NOTE = "Not executed: the step limit was reached."


@dataclass
class AgentResult:
    answer: str
    status: str  # answered | max_steps | refused | error
    sources: list[str]  # the successful API requests behind the answer, in order
    steps: list[Step]
    pending_action: PendingAction | None  # a proposed alert waiting for the user, if any
    usage: Usage
    cost_usd: float | None
    duration_ms: int
    error: str | None = None  # status "error": why the model could not be used (safe to show)
    messages: list[dict[str, Any]] = field(repr=False, default_factory=list)  # for follow-ups
    transcript: Transcript | None = field(repr=False, default=None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "status": self.status,
            "sources": self.sources,
            "pending_action": None if self.pending_action is None else vars(self.pending_action),
            "usage": self.usage.to_dict(),
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "steps": len(self.steps),
        }


class Agent:
    def __init__(
        self,
        model: Model,
        toolbox: ToolBox,
        max_steps: int = 8,
        currencies: CurrencyContext | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = model
        self._tools = toolbox
        self._max_steps = max_steps
        self._currencies = currencies or CurrencyContext(toolbox)
        self._clock = clock

    @property
    def model_name(self) -> str:
        return self._model.name

    def close(self) -> None:
        """Release the HTTP connection the tools use."""
        self._tools.close()

    def run(self, question: str, history: list[dict[str, Any]] | None = None) -> AgentResult:
        """Answer one question. For a follow-up, pass the previous result's `messages`."""
        started = self._clock()
        system = build_system_prompt(self._currencies.get())
        # Copy, then append. We never modify what came before.
        messages: list[dict[str, Any]] = [*(history or []), {"role": "user", "content": question}]

        transcript = Transcript(question=question, model=self._model.name)
        usage = Usage()
        sources: list[str] = []
        pending: PendingAction | None = None

        def finish(answer: str, status: str, error: str | None = None) -> AgentResult:
            return AgentResult(
                answer=answer,
                status=status,
                sources=sources,
                steps=transcript.steps,
                # A failed run never showed its answer to the user, so a proposal made earlier in
                # it must not be offered for confirmation either.
                pending_action=None if status == "error" else pending,
                usage=usage,
                cost_usd=estimate_cost_usd(self._model.name, usage),
                duration_ms=int((self._clock() - started) * 1000),
                error=error,
                messages=messages,
                transcript=transcript,
            )

        for step_number in range(1, self._max_steps + 1):
            # 1. Ask the model. (Retries for rate limits / outages happen inside the model.)
            t0 = self._clock()
            try:
                reply = self._model.complete(system, messages, TOOL_DEFINITIONS)
            except ModelError as exc:
                logger.error("Model call failed: %s", exc)
                return finish(f"Sorry, I couldn't get an answer: {exc} Nothing was changed.", "error")
            usage.add(reply.usage)

            # 2. Remember what the model said, exactly as it said it.
            messages.append({"role": "assistant", "content": reply.content})
            text = "\n".join(b["text"] for b in reply.content if b["type"] == "text").strip()
            tool_calls = [b for b in reply.content if b["type"] == "tool_use"]
            step = Step(
                step=step_number,
                model_text=text,
                input_tokens=reply.usage.input_tokens,
                output_tokens=reply.usage.output_tokens,
                model_duration_ms=int((self._clock() - t0) * 1000),
                stop_reason=reply.stop_reason,
            )
            transcript.steps.append(step)

            # 3. No tool requested? Then the model is done, and its text is the answer.
            if reply.stop_reason == "refusal":
                return finish("I can't help with that request.", "refused")
            if not tool_calls:
                answer = text or "I couldn't produce an answer."
                if reply.stop_reason == "max_tokens":
                    answer += "\n\n(The answer was cut off because it got too long.)"
                return finish(answer, "answered")

            # 4. Out of steps but the model still wants tools: stop, don't run them.
            #    We still answer every tool request (history must stay valid for follow-ups).
            if step_number == self._max_steps:
                messages.append({
                    "role": "user",
                    "content": [_tool_result_block(c["id"], STEP_LIMIT_NOTE, is_error=True) for c in tool_calls],
                })
                return finish(_step_limit_answer(self._max_steps, transcript.steps), "max_steps")

            # 5. Run the requested tools and send ALL results back in ONE user message.
            result_blocks = []
            for call in tool_calls:
                t1 = self._clock()
                result = self._run_tool(call["name"], call.get("input") or {}, pending)
                if result.pending_action is not None:
                    pending = result.pending_action
                if result.source and result.status == 200:
                    if result.source not in sources:
                        sources.append(result.source)
                step.tool_calls.append(
                    ToolCallRecord(
                        tool=call["name"],
                        input=call.get("input") or {},
                        request=result.source,
                        status=result.status if result.status is not None else (
                            "pending" if result.pending_action else None
                        ),
                        result_summary=summarize(result.content),
                        is_error=result.is_error,
                        duration_ms=int((self._clock() - t1) * 1000),
                    )
                )
                result_blocks.append(
                    _tool_result_block(call["id"], _text_for_model(result), result.is_error)
                )
            messages.append({"role": "user", "content": result_blocks})
            # ...and around we go: the model now sees the results and decides what next.

        raise AssertionError("unreachable: the last step always returns")  # pragma: no cover

    def _run_tool(self, name: str, tool_input: dict[str, Any], pending: PendingAction | None) -> ToolResult:
        """Run one tool. Never raises: a crash becomes an error result the model can read."""
        try:
            result = self._tools.run(name, tool_input)
        except Exception:  # a bug in a tool must not kill the whole run
            logger.exception("Tool %s crashed", name)
            return ToolResult(content=f"Error: the tool '{name}' failed unexpectedly.", is_error=True)
        if result.pending_action is not None and pending is not None:
            # One proposal at a time: the user confirms or cancels it first.
            return ToolResult(
                content="Error: another alert is already waiting for the user's confirmation. "
                "Ask them to confirm or cancel it first; propose this one afterwards.",
                is_error=True,
            )
        return result


def _text_for_model(result: ToolResult) -> str:
    """What the model reads: the request that was made (so it can cite it), then the result."""
    return f"{result.source}\n{result.content}" if result.source else result.content


def _tool_result_block(tool_use_id: str, content: str, is_error: bool) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def _step_limit_answer(max_steps: int, steps: list[Step]) -> str:
    """Honest fallback when the model never finished: list what the tools did find."""
    lines = [f"I couldn't finish within {max_steps} steps. Here's what I found so far:"]
    found = [c for s in steps for c in s.tool_calls if not c.is_error and c.request]
    for c in found:
        lines.append(f"- {c.request}: {c.result_summary}")
    if not found:
        lines.append("- Nothing usable yet.")
    lines.append("Try a narrower question.")
    return "\n".join(lines)
