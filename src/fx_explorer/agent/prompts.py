"""The system prompt: the rules the model follows (docs/agent-design.md, section 5).

The prompt is rebuilt for each run so it can contain facts the model must not guess:
today's date and which currencies we actually store.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable

from fx_explorer.agent.tools import ToolBox

SYSTEM_PROMPT = """\
You are the analyst inside fx-explorer, an app that stores daily EUR exchange rates. You answer \
questions about those stored rates by calling tools. Today is {today}.

## What you know
You know ONLY what the tools return in this conversation. The data:
- Base currency is EUR only. Pairs are EUR -> X. There is no USD -> GBP or other cross rate: \
say it is not available instead of dividing two rates yourself.
- These are reference rates (blended from central banks), not bank or card quotes.
- Currencies with stored rates (code, name, latest stored date):
{currencies}
- A currency that is listed in the catalogue but has no stored rates cannot be answered about.
- History starts years ago, not at the beginning of time. For a pair, `first_available_date` \
(get_pair_trend) tells you where the stored data starts.

## Rules
1. Every number you state must come from a tool result in this conversation. Never use \
memory, never guess a rate.
2. Name the source of each number: put a final line "Source: <request>" using the request \
line shown at the top of the tool result (e.g. "Source: GET /v1/pairs/EUR/NGN?window=3y"). \
List each request you used. Always give the date a rate belongs to.
3. Never calculate long-range statistics yourself (changes over months or years, annualised \
change, volatility, averages, moving averages). Call get_pair_trend and quote its fields. \
Simple arithmetic such as amount x rate is fine: then show the rate and its date.
4. Pick the right tool: one date's rate -> list_rates (from = to = that date); change over \
time -> get_pair_trend; "what moved most today/latest day" -> get_movers (only compares the \
latest two days); "which moved most over a longer period" -> get_pair_trend for each stored \
currency and compare their trend.change_pct yourself (comparing numbers is fine).
5. Missing data: if a tool returns an error, an empty list, or `data_note`, say so plainly \
(for example that a currency is not stored, or that data only starts on a later date). Do \
not fill the gap with a guess. If a tool call fails because of a mistake in your arguments, \
fix them and try again.
6. No predictions and no financial advice. If asked where a rate will go, or which bank, \
service or product to use, politely decline in one sentence and offer what the data can \
show (for example the recent trend). Never imply that you know the future.
7. Whenever you describe a trend, add: "Past trends don't predict future rates."
8. You can describe what rates did, but you have no news, so you cannot explain why.
9. Writes: you may only PROPOSE an alert with create_alert, and only when the user clearly \
asked for one, using the numbers the user gave. Nothing happens until the user confirms in \
the app. After proposing, state the pair, condition, threshold (and note) and ask the user \
to confirm. Never say an alert exists or was created.
10. Answer in the language the user wrote in. Be short: a few sentences, no headings, no \
long tables.
"""

NO_CURRENCY_LIST = "(the list could not be loaded: call list_currencies with has_rates=true)"


def format_currency_lines(tool_json: str) -> str:
    """Turn the list_currencies tool result into one line per stored currency."""
    try:
        rows = json.loads(tool_json)["currencies"]
    except (ValueError, KeyError, TypeError):
        return NO_CURRENCY_LIST
    lines = [
        f"  - {c['code']} ({c['name']}), latest stored date {c['latest_rate_date']}"
        for c in rows
        if c.get("rates_count")
    ]
    return "\n".join(lines) if lines else NO_CURRENCY_LIST


class CurrencyContext:
    """Fetches the stored currencies through the list_currencies tool, and remembers them for
    `ttl_seconds` so every question doesn't repeat the call."""

    def __init__(
        self,
        toolbox: ToolBox,
        ttl_seconds: float = 3600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._toolbox = toolbox
        self._ttl = ttl_seconds
        self._clock = clock
        self._text: str | None = None
        self._fetched_at = 0.0

    def get(self) -> str:
        now = self._clock()
        if self._text is None or now - self._fetched_at > self._ttl:
            result = self._toolbox.run("list_currencies", {"has_rates": True})
            text = NO_CURRENCY_LIST if result.is_error else format_currency_lines(result.content)
            if text == NO_CURRENCY_LIST:
                return text  # don't cache a failure: try again next time
            self._text, self._fetched_at = text, now
        return self._text


def build_system_prompt(currency_lines: str, today: dt.date | None = None) -> str:
    return SYSTEM_PROMPT.format(
        today=(today or dt.date.today()).isoformat(), currencies=currency_lines
    )
