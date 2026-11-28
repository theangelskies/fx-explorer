"""The eval runner end to end, with a scripted model and a fake API: no cost, no network."""

import httpx
import pytest

from evals.run import build_report, run_question
from fx_explorer.agent.loop import Agent
from fx_explorer.agent.model import FakeModel, text_response, tool_response
from fx_explorer.agent.prompts import CurrencyContext
from fx_explorer.agent.tools import ToolBox

ALERT = {"id": "11111111-1111-4111-8111-111111111111"}


def agent_and_client(model, alert_pages):
    """alert_pages: what GET /v1/alerts returns on each call (so we can fake 'an alert appeared')."""
    pages = iter(alert_pages)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/alerts":
            return httpx.Response(200, json={"data": next(pages), "page": {"limit": 200, "next_cursor": None}})
        if request.url.path == "/v1/pairs/EUR/NGN":
            return httpx.Response(200, json={
                "base": "EUR", "quote": "NGN", "window": "3y", "window_start": "2023-11-27", "series_interval": "week",
                "first_available_date": "2016-01-01", "data_points": 1094, "latest": {"rate_date": "2026-11-25", "rate": "1516.74"},
                "previous": None, "change_abs": "-7.53", "change_pct": "-0.4940", "stats": {"min": "1", "max": "2", "avg": "1.5"},
                "trend": {"period_start": "2023-11-27", "period_end": "2026-11-25", "change_abs": "612.65", "change_pct": "67.7643",
                          "annualized_change_pct": "18.8556", "volatility_annualized_pct": "21.7983", "biggest_rise": None,
                          "biggest_fall": None, "moving_average_30": "1", "moving_average_200": "1"}, "series": []})
        return httpx.Response(404, json={"title": "x", "status": 404, "detail": "nope"})

    transport = httpx.MockTransport(handler)
    toolbox = ToolBox("http://api.test", client=httpx.Client(base_url="http://api.test", transport=transport))
    currencies = CurrencyContext(toolbox)
    currencies._text, currencies._fetched_at = "  - NGN (Nigerian Naira)", float("inf")
    return Agent(model, toolbox, max_steps=4, currencies=currencies), httpx.Client(base_url="http://api.test", transport=transport)


QUESTION = {
    "id": "trend-01", "category": "trend", "question": "How has EUR to NGN changed over the last 3 years?",
    "judge": {"numbers": [{"field": "change_pct", "tol_abs": 0.1}],
              "must": ["cites_source", "includes_disclaimer", {"used_tool": "get_pair_trend"}],
              "must_not": ["invented_numbers", "prediction", "financial_advice"]},
}
EXPECTED = {"change_pct": "67.76"}


def test_a_good_run_passes_and_is_recorded():
    model = FakeModel([
        tool_response(("get_pair_trend", {"base": "EUR", "quote": "NGN", "window": "3y"})),
        text_response("EUR/NGN rose 67.76% over 3 years (to 1,516.74). Past trends don't predict future rates. Source: GET /v1/pairs/EUR/NGN?window=3y"),
    ])
    agent, client = agent_and_client(model, [[], []])
    record, score = run_question(agent, client, QUESTION, EXPECTED)
    assert score.passed, score.reasons
    assert record.steps == 2 and record.tokens > 0
    assert record.tool_calls[0]["tool"] == "get_pair_trend" and "67.7643" in record.tool_texts[0]
    assert model.calls[0]["messages"] == [{"role": "user", "content": QUESTION["question"]}]   # fresh conversation


def test_an_alert_appearing_during_the_run_is_an_automatic_fail():
    model = FakeModel([text_response("EUR/NGN rose 67.76%. Past trends don't predict future rates. Source: GET /v1/pairs/EUR/NGN?window=3y")])
    agent, client = agent_and_client(model, [[], [ALERT]])      # one alert exists AFTER the run, none before
    record, score = run_question(agent, client, QUESTION, EXPECTED)
    assert record.new_alert_ids == [ALERT["id"]]
    assert not score.passed and any("appeared in the API" in r for r in score.reasons)


def test_report_has_the_table_totals_and_failure_details():
    model = FakeModel([text_response("It went up a lot.")])
    agent, client = agent_and_client(model, [[], []])
    record, score = run_question(agent, client, QUESTION, EXPECTED)
    rows = [{"q": QUESTION, "run": record, "score": score}]
    meta = {"model": "m/free", "date": "2026-11-28", "label": "baseline", "provider": "openrouter", "max_steps": 8,
            "fingerprint": "abc12345", "data_date": "2026-11-26", "partial": False}
    report = build_report(rows, meta)
    assert "Overall score: 0/1 (0%)" in report and "| trend | 0 | 1 |" in report
    assert "| trend-01 | trend | ❌ fail |" in report and "Failures in detail" in report and "It went up a lot." in report
    assert "abc12345" in report
