"""A recording of one agent run: every step, for debugging, the web UI and the evals.

One STEP = one model call, plus any tools the model asked for in that turn.
Nothing here ever holds the API key (the key is only inside the Anthropic client).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SUMMARY_CHARS = 240


def summarize(text: str, limit: int = SUMMARY_CHARS) -> str:
    """The first `limit` characters of a tool result, on one line."""
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


@dataclass
class ToolCallRecord:
    tool: str
    input: dict[str, Any]
    request: str | None  # e.g. "GET /v1/movers?base=EUR" (None: no HTTP request was made)
    status: int | str | None  # HTTP status, or "pending" for a proposed write
    result_summary: str
    is_error: bool
    duration_ms: int


@dataclass
class Step:
    step: int  # 1, 2, 3 ...
    model_text: str  # whatever the model wrote this turn
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model_duration_ms: int = 0
    stop_reason: str = ""


@dataclass
class Transcript:
    question: str
    model: str
    steps: list[Step] = field(default_factory=list)
    started_at: str = field(
        default_factory=lambda: dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, directory: Path, result: dict[str, Any] | None = None) -> Path:
        """Write the transcript (and optionally the final result) as JSON. Returns the path."""
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self.started_at.replace(":", "").replace("-", "")
        path = directory / f"{stamp}-{abs(hash(self.question)) % 10_000:04d}.json"
        path.write_text(json.dumps({**self.to_dict(), "result": result}, indent=2, ensure_ascii=False) + "\n")
        return path
