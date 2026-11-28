"""AnthropicModel retry behaviour (with a fake client: no network, no cost) and the prompt helpers."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from fx_explorer.agent.model import AnthropicModel, ModelError, Usage, estimate_cost_usd
from fx_explorer.agent.prompts import NO_CURRENCY_LIST, CurrencyContext, build_system_prompt, format_currency_lines
from fx_explorer.agent.tools import ToolBox

REQUEST = httpx2.Request("POST", "https://api.test/v1/messages")


def status_error(cls, status, headers=None):
    return cls("boom", response=httpx2.Response(status, request=REQUEST, headers=headers or {}), body=None)


def sdk_response():
    return SimpleNamespace(
        content=[SimpleNamespace(model_dump=lambda **kw: {"type": "text", "text": "hi"})],
        stop_reason="end_turn", model="claude-sonnet-5-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=None, cache_creation_input_tokens=0),
    )


class ScriptedClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def model_with(script, max_retries=3):
    sleeps = []
    client = ScriptedClient(script)
    return AnthropicModel("claude-sonnet-5-5", None, max_retries=max_retries, client=client, sleep=sleeps.append), client, sleeps


def test_success_converts_the_response():
    model, client, _ = model_with([sdk_response()])
    out = model.complete("sys", [{"role": "user", "content": "q"}], [])
    assert out.content == [{"type": "text", "text": "hi"}] and out.stop_reason == "end_turn"
    assert out.usage == Usage(10, 5, 0, 0)
    sent = client.calls[0]
    assert sent["model"] == "claude-sonnet-5-5" and "temperature" not in sent and "tool_choice" not in sent


def test_transient_errors_are_retried_with_backoff():
    errors = [status_error(anthropic.RateLimitError, 429, {"retry-after": "7"}),
              status_error(anthropic.InternalServerError, 500),
              anthropic.APIConnectionError(request=REQUEST)]
    model, client, sleeps = model_with([*errors, sdk_response()])
    assert model.complete("s", [], []).stop_reason == "end_turn"
    assert len(client.calls) == 4 and len(sleeps) == 3
    assert sleeps[0] == 7.0                  # Retry-After is honoured
    assert 2 <= sleeps[2] < 5.1              # exponential backoff with jitter (attempt 2: ~4s)


def test_gives_up_after_max_retries_with_a_clear_message():
    errors = [status_error(anthropic.InternalServerError, 500) for _ in range(3)]
    model, client, sleeps = model_with(errors, max_retries=2)
    with pytest.raises(ModelError, match="unavailable right now.*3 attempts"):
        model.complete("s", [], [])
    assert len(client.calls) == 3 and len(sleeps) == 2


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (status_error(anthropic.AuthenticationError, 401), "API key was rejected"),
        (status_error(anthropic.NotFoundError, 404), "was not found"),
        (status_error(anthropic.BadRequestError, 400), r"rejected \(400\)"),
    ],
)
def test_permanent_errors_are_not_retried(error, message):
    model, client, sleeps = model_with([error])
    with pytest.raises(ModelError, match=message):
        model.complete("s", [], [])
    assert len(client.calls) == 1 and sleeps == []


def test_missing_api_key_is_a_clear_error_that_never_prints_a_key():
    with pytest.raises(ModelError, match="ANTHROPIC_API_KEY is not set"):
        AnthropicModel("claude-sonnet-5-5", None)


def test_cost_estimate():
    usage = Usage(input_tokens=1000, output_tokens=500, cache_read_tokens=2000, cache_write_tokens=400)
    expected = (1000 * 2 + 2000 * 0.2 + 400 * 2.5 + 500 * 10) / 1_000_000
    assert estimate_cost_usd("claude-sonnet-5-5", usage) == pytest.approx(expected)
    assert estimate_cost_usd("mystery-model", usage) is None


# ------------------------------------------------------------------ prompts

CURRENCIES = json.dumps({"currencies": [
    {"code": "NGN", "name": "Nigerian Naira", "type": "currency", "rates_count": 3601, "latest_rate_date": "2026-11-25"},
    {"code": "SEK", "name": "Swedish Krona", "type": "currency", "rates_count": 0, "latest_rate_date": None},
]})


def test_currency_lines_skip_currencies_without_rates():
    text = format_currency_lines(CURRENCIES)
    assert "NGN (Nigerian Naira), latest stored date 2026-11-25" in text and "SEK" not in text
    assert format_currency_lines("not json") == NO_CURRENCY_LIST


def test_currency_context_is_cached_and_retried_after_a_failure():
    import respx

    with respx.mock(base_url="http://api.test") as api:
        route = api.get("/v1/currencies").respond(200, json={"data": [{
            "iso_code": "NGN", "name": "Nigerian Naira", "type": "currency", "iso_numeric": None, "symbol": None,
            "start_date": None, "end_date": None, "rates_count": 5, "latest_rate_date": "2026-11-25"}],
            "page": {"limit": 50, "next_cursor": None}})
        now = [0.0]
        ctx = CurrencyContext(ToolBox("http://api.test"), ttl_seconds=100, clock=lambda: now[0])
        assert "NGN" in ctx.get() and "NGN" in ctx.get()
        assert route.call_count == 1                      # second call came from the cache
        now[0] = 101
        ctx.get()
        assert route.call_count == 2                      # expired: fetched again
        route.respond(503, json={"title": "x", "status": 503, "detail": "down"})
        now[0] = 500
        assert ctx.get() == NO_CURRENCY_LIST              # failure: not cached as success
        route.respond(200, json={"data": [], "page": {"limit": 50, "next_cursor": None}})
        assert ctx.get() == NO_CURRENCY_LIST              # empty list is not cached either


def test_system_prompt_contains_the_rules_and_facts():
    import datetime as dt

    prompt = build_system_prompt("  - NGN (Nigerian Naira), latest stored date 2026-11-25", dt.date(2026, 11, 28))
    assert "Today is 2026-11-28." in prompt and "NGN (Nigerian Naira)" in prompt
    for rule in ("Past trends don't predict future rates.", "No predictions and no financial advice",
                 "PROPOSE an alert", "Answer in the language the user wrote in", "Never calculate long-range statistics"):
        assert rule in prompt
