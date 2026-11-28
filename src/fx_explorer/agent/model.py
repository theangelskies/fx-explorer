"""A thin wrapper around the language model, so the rest of the agent never imports `anthropic`.

    complete(system, messages, tools) -> ModelResponse

Two implementations:
  * AnthropicModel: the real thing (costs money; needs ANTHROPIC_API_KEY).
  * FakeModel: returns scripted answers. Every test uses this, so tests are free and exact.

Messages and content blocks are plain dicts, the same JSON shape the API uses:
    {"role": "user", "content": "How has EUR/NGN moved?"}
    {"role": "assistant", "content": [{"type": "text", ...}, {"type": "tool_use", ...}]}
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., "content": ...}]}
"""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

# Reuse the Week 1 backoff ideas: 1s, 2s, 4s... plus jitter, and honour Retry-After.
from fx_explorer.frankfurter.client import _backoff_seconds, _parse_retry_after

logger = logging.getLogger(__name__)

# $ per 1M tokens (input, output) for prompts up to 100K tokens. Cache reads cost 0.1x the
# input price, cache writes 1.25x. Update when prices change; unknown models give cost None.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.00, 20.00),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


def estimate_cost_usd(model: str, usage: Usage) -> float | None:
    """A rough price from the token counts. None if we don't know this model's price."""
    price = PRICES_PER_MTOK.get(model)
    if price is None:
        return None
    in_price, out_price = price
    return (
        usage.input_tokens * in_price
        + usage.cache_read_tokens * in_price * 0.1
        + usage.cache_write_tokens * in_price * 1.25
        + usage.output_tokens * out_price
    ) / 1_000_000


@dataclass
class ModelResponse:
    content: list[dict[str, Any]]  # blocks: text / tool_use / thinking ...
    stop_reason: str  # end_turn | tool_use | max_tokens | refusal | ...
    usage: Usage = field(default_factory=Usage)
    model: str = ""


class ModelError(Exception):
    """The model could not be used (after retries). `message` is safe to show to the user:
    it never contains the API key."""


class Model(Protocol):
    name: str

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse: ...


# ------------------------------------------------------------------ the real model


class AnthropicModel:
    """Calls the Anthropic Messages API. We do the retrying ourselves (SDK retries are off)
    so the behaviour is visible and testable."""

    def __init__(
        self,
        model: str,
        api_key: str | None,
        max_retries: int = 3,
        max_tokens: int = 4096,
        effort: str = "medium",
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = model
        self._max_retries = max_retries
        self._max_tokens = max_tokens
        self._effort = effort
        self._sleep = sleep
        if client is None:
            if not api_key:
                raise ModelError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
            client = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=60.0)
        self._client = client

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.messages.create(
                    model=self.name,
                    max_tokens=self._max_tokens,
                    system=system,
                    messages=messages,
                    tools=tools,
                    # Cache the repeated start of the request (tools + system + earlier
                    # steps): every loop step re-sends it, and cached input is 10x cheaper.
                    cache_control={"type": "ephemeral"},
                    output_config={"effort": self._effort},
                )
                return _to_model_response(response)
            except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
                self._wait_or_raise(attempt, exc, _retry_after(exc))
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
                self._wait_or_raise(attempt, exc, None)
            except anthropic.AuthenticationError as exc:
                raise ModelError("The Anthropic API key was rejected (check ANTHROPIC_API_KEY).") from exc
            except anthropic.PermissionDeniedError as exc:
                raise ModelError("The Anthropic API key is not allowed to use this model.") from exc
            except anthropic.NotFoundError as exc:
                raise ModelError(f"Model '{self.name}' was not found (check AGENT_MODEL).") from exc
            except anthropic.APIStatusError as exc:
                if exc.status_code == 529:  # "overloaded": worth retrying like a 5xx
                    self._wait_or_raise(attempt, exc, _retry_after(exc))
                else:
                    raise ModelError(f"The model request was rejected ({exc.status_code}): {exc.message}") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait_or_raise(self, attempt: int, exc: Exception, retry_after: float | None) -> None:
        if attempt >= self._max_retries:
            raise ModelError(
                f"The AI model is unavailable right now ({type(exc).__name__}) "
                f"after {attempt + 1} attempts."
            ) from exc
        wait = retry_after if retry_after is not None else _backoff_seconds(attempt)
        logger.warning("Model call failed (%s); retrying in %.1fs", type(exc).__name__, wait)
        self._sleep(wait)


def _retry_after(exc: anthropic.APIStatusError) -> float | None:
    return _parse_retry_after(exc.response.headers.get("retry-after"))


def _to_model_response(response: Any) -> ModelResponse:
    usage = response.usage
    return ModelResponse(
        # model_dump -> plain dicts. exclude_none drops empty fields the API wouldn't expect
        # back. Thinking blocks (with their signature) are kept exactly as received.
        content=[block.model_dump(mode="json", exclude_none=True) for block in response.content],
        stop_reason=response.stop_reason or "",
        usage=Usage(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        ),
        model=response.model,
    )


# ------------------------------------------------------------------ the fake model (tests)


class FakeModel:
    """Plays back a script. Each complete() call returns the next item; an Exception item is
    raised instead. Remembers what it was sent, so tests can look at it."""

    name = "fake-model"

    def __init__(self, script: list[ModelResponse | Exception]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        # deepcopy: the loop keeps appending to `messages`; we want what was sent AT THIS call
        self.calls.append(
            {"system": system, "messages": copy.deepcopy(messages), "tools": tools}
        )
        if not self._script:
            raise AssertionError("FakeModel ran out of scripted responses")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def text_response(text: str, **usage: int) -> ModelResponse:
    """Test helper: the model answers in plain text and stops."""
    return ModelResponse(
        content=[{"type": "text", "text": text}],
        stop_reason="end_turn",
        usage=Usage(**{"input_tokens": 100, "output_tokens": 20, **usage}),
        model="fake-model",
    )


def tool_response(*calls: tuple[str, dict[str, Any]], text: str = "", n: int = 0) -> ModelResponse:
    """Test helper: the model asks for one or more tools (optionally with some text first)."""
    content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    for i, (name, tool_input) in enumerate(calls):
        content.append({"type": "tool_use", "id": f"toolu_{n}_{i}", "name": name, "input": tool_input})
    return ModelResponse(
        content=content,
        stop_reason="tool_use",
        usage=Usage(input_tokens=100, output_tokens=30),
        model="fake-model",
    )
