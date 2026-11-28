"""Tool executor tests. respx fakes the API: nothing here touches the network or the database."""

import json

import httpx
import pytest
import respx

from fx_explorer.agent.tools import MAX_ROWS_TO_MODEL, ToolBox

API = "http://api.test"


@pytest.fixture
def api():
    with respx.mock(base_url=API, assert_all_called=False) as router:
        yield router


@pytest.fixture
def tools():
    box = ToolBox(API, timeout_seconds=1)
    yield box
    box.close()


def last_request(route) -> httpx.Request:
    return route.calls.last.request


def problem(status: int, title: str, detail: str, errors=None) -> httpx.Response:
    body = {"type": "/problems/x", "title": title, "status": status, "detail": detail, "instance": "/x"}
    if errors:
        body["errors"] = errors
    return httpx.Response(status, json=body, headers={"content-type": "application/problem+json"})


PAIR = {
    "base": "EUR", "quote": "NGN", "window": "3y", "window_start": "2023-11-27",
    "series_interval": "week", "first_available_date": "2016-01-01", "data_points": 1094,
    "latest": {"rate_date": "2026-11-25", "rate": "1516.74"},
    "previous": {"rate_date": "2026-11-24", "rate": "1524.27"},
    "change_abs": "-7.53", "change_pct": "-0.4940",
    "stats": {"min": "886.61", "max": "1852.8", "avg": "1611.98"},
    "trend": {
        "period_start": "2023-11-27", "period_end": "2026-11-25", "change_abs": "612.65",
        "change_pct": "67.7643", "annualized_change_pct": "18.8556",
        "volatility_annualized_pct": "21.7983", "biggest_rise": {"date": "2024-02-01", "change_pct": "24.7856"},
        "biggest_fall": {"date": "2024-05-22", "change_pct": "-5.4314"},
        "moving_average_30": "1506.95", "moving_average_200": "1549.07",
    },
    "series": [{"date": f"2024-{1 + n // 28:02d}-{1 + n % 28:02d}", "rate": str(900 + n)} for n in range(100)],
}


# ------------------------------------------------------------------ request URLs and params


def test_get_pair_trend_request(api, tools):
    route = api.get("/v1/pairs/EUR/NGN").respond(200, json=PAIR)
    result = tools.run("get_pair_trend", {"base": "eur", "quote": "NGN", "window": "3y"})
    assert dict(last_request(route).url.params) == {"window": "3y"}
    assert result.source == "GET /v1/pairs/EUR/NGN?window=3y"
    assert result.status == 200 and not result.is_error


def test_get_pair_trend_without_window_sends_no_window(api, tools):
    route = api.get("/v1/pairs/EUR/USD").respond(200, json=PAIR)
    result = tools.run("get_pair_trend", {"base": "EUR", "quote": "USD"})
    assert dict(last_request(route).url.params) == {}
    assert result.source == "GET /v1/pairs/EUR/USD"


def test_list_rates_request(api, tools):
    route = api.get("/v1/rates").respond(200, json={"data": [], "page": {"limit": 5, "next_cursor": None}})
    result = tools.run(
        "list_rates",
        {"base": "EUR", "quotes": ["usd", "GBP"], "from": "2025-03-14", "to": "2025-03-14", "limit": 5},
    )
    assert dict(last_request(route).url.params) == {
        "base": "EUR", "quotes": "USD,GBP", "from": "2025-03-14", "to": "2025-03-14", "limit": "5",
    }
    assert result.source == "GET /v1/rates?base=EUR&quotes=USD,GBP&from=2025-03-14&to=2025-03-14&limit=5"


def test_list_rates_defaults_to_eur_and_30_rows(api, tools):
    route = api.get("/v1/rates").respond(200, json={"data": [], "page": {"limit": 30, "next_cursor": None}})
    tools.run("list_rates", {})
    assert dict(last_request(route).url.params) == {"base": "EUR", "limit": "30"}


def test_list_rates_accepts_quotes_as_a_comma_string(api, tools):
    route = api.get("/v1/rates").respond(200, json={"data": [], "page": {"limit": 30, "next_cursor": None}})
    tools.run("list_rates", {"quotes": "USD, GBP"})
    assert last_request(route).url.params["quotes"] == "USD,GBP"


def test_get_movers_request(api, tools):
    route = api.get("/v1/movers").respond(200, json={"base": "EUR", "data": []})
    tools.run("get_movers", {"base": "EUR", "limit": 3, "direction": "down"})
    assert dict(last_request(route).url.params) == {"base": "EUR", "limit": "3", "direction": "down"}


def test_list_currencies_request_sends_booleans_as_true_false(api, tools):
    route = api.get("/v1/currencies").respond(200, json={"data": [], "page": {"limit": 50, "next_cursor": None}})
    tools.run("list_currencies", {"q": "naira", "has_rates": True})
    assert dict(last_request(route).url.params) == {"q": "naira", "has_rates": "true"}


def test_list_alerts_request(api, tools):
    route = api.get("/v1/alerts").respond(200, json={"data": [], "page": {"limit": 50, "next_cursor": None}})
    tools.run("list_alerts", {"status": "waiting"})
    assert dict(last_request(route).url.params) == {"status": "waiting"}


# ------------------------------------------------------------------ result trimming


def test_pair_result_is_trimmed_and_renamed(api, tools):
    api.get("/v1/pairs/EUR/NGN").respond(200, json=PAIR)
    out = json.loads(tools.run("get_pair_trend", {"base": "EUR", "quote": "NGN", "window": "3y"}).content)
    assert len(out["series_sample"]) <= 12
    assert out["series_sample"][0] == PAIR["series"][0]
    assert out["series_sample"][-1] == PAIR["series"][-1]
    assert "series" not in out and "series_interval" not in out
    assert out["trend"]["change_pct"] == "67.7643"            # passed through untouched
    assert out["latest_day_change_pct"] == "-0.4940"          # renamed: not the window change
    assert "change_pct" not in out
    assert "data_note" not in out


def test_pair_result_warns_when_data_starts_after_the_window(api, tools):
    late = {**PAIR, "trend": {**PAIR["trend"], "period_start": "2024-06-01"}}
    api.get("/v1/pairs/EUR/NGN").respond(200, json=late)
    out = json.loads(tools.run("get_pair_trend", {"base": "EUR", "quote": "NGN", "window": "3y"}).content)
    assert "2024-06-01" in out["data_note"]


def test_pair_with_a_short_series_is_not_resampled(api, tools):
    short = {**PAIR, "series": PAIR["series"][:5]}
    api.get("/v1/pairs/EUR/NGN").respond(200, json=short)
    out = json.loads(tools.run("get_pair_trend", {"base": "EUR", "quote": "NGN"}).content)
    assert out["series_sample"] == short["series"]


def rate_rows(n):
    return [
        {"rate_date": "2025-03-14", "base": "EUR", "quote": "USD", "rate": "1.0858", "updated_at": "2026-11-26T00:00:00Z"}
    ] * n


def test_rates_result_keeps_only_useful_fields(api, tools):
    api.get("/v1/rates").respond(200, json={"data": rate_rows(1), "page": {"limit": 30, "next_cursor": None}})
    out = json.loads(tools.run("list_rates", {"from": "2025-03-14", "to": "2025-03-14"}).content)
    assert out == {
        "base": "EUR", "count": 1, "more_results_exist": False,
        "rates": [{"date": "2025-03-14", "quote": "USD", "rate": "1.0858"}],
    }


def test_rates_result_is_cut_when_very_long(api, tools):
    api.get("/v1/rates").respond(200, json={"data": rate_rows(150), "page": {"limit": 200, "next_cursor": "abc"}})
    out = json.loads(tools.run("list_rates", {"limit": 200}).content)
    assert len(out["rates"]) == MAX_ROWS_TO_MODEL
    assert "truncated" in out and out["more_results_exist"] is True


def test_empty_rates_say_so(api, tools):
    api.get("/v1/rates").respond(200, json={"data": [], "page": {"limit": 30, "next_cursor": None}})
    out = json.loads(tools.run("list_rates", {"from": "2010-06-01", "to": "2010-06-01"}).content)
    assert out["rates"] == [] and "No stored rates" in out["note"]


def test_movers_result(api, tools):
    api.get("/v1/movers").respond(200, json={"base": "EUR", "data": [{
        "quote": "NGN", "rate_date": "2026-11-25", "rate": "1516.74", "previous_rate_date": "2026-11-24",
        "previous_rate": "1524.27", "change_abs": "-7.53", "change_pct": "-0.4940"}]})
    out = json.loads(tools.run("get_movers", {}).content)
    assert out["movers"] == [{"quote": "NGN", "date": "2026-11-25", "rate": "1516.74",
                              "previous_date": "2026-11-24", "previous_rate": "1524.27", "change_pct": "-0.4940"}]


def test_currencies_result_shows_whether_rates_are_stored(api, tools):
    api.get("/v1/currencies").respond(200, json={"data": [{
        "iso_code": "SEK", "name": "Swedish Krona", "type": "currency", "iso_numeric": "752", "symbol": "kr",
        "start_date": "1949-12-21", "end_date": None, "rates_count": 0, "latest_rate_date": None}],
        "page": {"limit": 50, "next_cursor": None}})
    out = json.loads(tools.run("list_currencies", {"q": "krona"}).content)
    assert out["currencies"] == [{"code": "SEK", "name": "Swedish Krona", "type": "currency",
                                  "rates_count": 0, "latest_rate_date": None}]


def test_alerts_result(api, tools):
    api.get("/v1/alerts").respond(200, json={"data": [{
        "id": "7f0c1d6e-0000-4000-8000-000000000001", "base": "EUR", "quote": "USD", "condition": "above",
        "threshold": "1.2", "note": None, "created_at": "2026-11-26T08:00:00Z", "current_rate": "1.0948",
        "triggered": False}], "page": {"limit": 50, "next_cursor": None}})
    out = json.loads(tools.run("list_alerts", {}).content)
    assert out["alerts"][0]["pair"] == "EUR/USD" and out["alerts"][0]["triggered"] is False


# ------------------------------------------------------------------ errors become readable results


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (problem(404, "Resource not found", "No rates stored for EUR/XYZ."), "Error 404: No rates stored for EUR/XYZ."),
        (problem(400, "Invalid query", "1 parameter is invalid.", [{"field": "from", "message": "Must be a valid date."}]),
         "Error 400: 1 parameter is invalid. from: Must be a valid date."),
        (problem(422, "Invalid body", "2 fields are invalid.", [{"field": "a", "message": "x"}, {"field": "b", "message": "y"}]),
         "Error 422: 2 fields are invalid. a: x b: y"),
        (problem(503, "Unavailable", "database down"), "Error 503: the API or its database is unavailable"),
        (httpx.Response(500, text="<html>oops</html>"), "Error 500: unexpected response from the API."),
    ],
)
def test_problem_responses_become_error_results(api, tools, response, expected):
    api.get("/v1/pairs/EUR/XYZ").mock(return_value=response)
    result = tools.run("get_pair_trend", {"base": "EUR", "quote": "XYZ"})
    assert result.is_error and result.status == response.status_code
    assert result.content.startswith(expected)
    assert result.source == "GET /v1/pairs/EUR/XYZ"


def test_timeout_becomes_an_error_result(api, tools):
    api.get("/v1/movers").mock(side_effect=httpx.ReadTimeout("slow"))
    result = tools.run("get_movers", {})
    assert result.is_error and "did not answer in time" in result.content


def test_connection_failure_becomes_an_error_result(api, tools):
    api.get("/v1/movers").mock(side_effect=httpx.ConnectError("refused"))
    result = tools.run("get_movers", {})
    assert result.is_error and "could not reach the API" in result.content


def test_unknown_tool_and_missing_argument_are_errors_not_exceptions(tools):
    assert "unknown tool" in tools.run("drop_database", {}).content
    assert "missing required argument 'quote'" in tools.run("get_pair_trend", {"base": "EUR"}).content


# ------------------------------------------------------------------ create_alert never calls the API


def test_create_alert_only_creates_a_pending_action(api, tools):
    result = tools.run(
        "create_alert",
        {"base": "eur", "quote": "USD", "condition": "above", "threshold": "1.20", "note": "trip"},
    )
    assert len(api.calls) == 0                       # not a single HTTP request
    action = result.pending_action
    assert action is not None and action.status == "pending" and action.tool == "create_alert"
    assert action.input == {"base": "EUR", "quote": "USD", "condition": "above", "threshold": "1.20", "note": "trip"}
    assert "NOT EXECUTED" in result.content and action.id in result.content
    assert result.status is None and result.source is None


def test_each_pending_action_gets_its_own_id(tools):
    args = {"base": "EUR", "quote": "USD", "condition": "above", "threshold": "1.2"}
    assert tools.run("create_alert", args).pending_action.id != tools.run("create_alert", args).pending_action.id


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"quote": "EUR"}, "different from base"),
        ({"condition": "over"}, "'above' or 'below'"),
        ({"threshold": "1,5"}, "plain decimal"),
        ({"threshold": "0"}, "plain decimal"),
        ({"threshold": "1e3"}, "plain decimal"),
        ({"note": "x" * 201}, "200 characters"),
        ({"colour": "red"}, "unknown fields"),
        ({"quote": ""}, "'quote' is required"),
    ],
)
def test_invalid_alerts_are_rejected_before_anything_is_proposed(api, tools, change, message):
    args = {"base": "EUR", "quote": "USD", "condition": "above", "threshold": "1.2", **change}
    result = tools.run("create_alert", args)
    assert result.is_error and result.pending_action is None and message in result.content
    assert len(api.calls) == 0
