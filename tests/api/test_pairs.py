"""GET /v1/pairs/{base}/{quote}."""

from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration


def test_pair_detail_matches_contract_example(seeded, contract):
    """Our seed data is the same as the contract's PairDetail example, so the API's answer
    must be exactly that example."""
    expected = contract["components"]["examples"]["PairDetailExample"]["value"]
    resp = seeded.get("/v1/pairs/EUR/USD?window=7d")
    assert resp.status_code == 200
    assert resp.json() == expected


def test_default_window_is_30d(seeded):
    body = seeded.get("/v1/pairs/EUR/USD").json()
    assert body["window"] == "30d"
    assert len(body["series"]) == 5


def test_window_ends_at_latest_stored_date(seeded):
    body = seeded.get("/v1/pairs/EUR/GBP?window=7d").json()
    assert body["series"][-1]["date"] == body["latest"]["rate_date"] == "2026-11-17"
    assert Decimal(body["change_abs"]) == 0  # the GBP rate didn't change
    assert body["change_pct"] == "0.0000"


def test_single_rate_has_null_previous_and_changes(seeded):
    body = seeded.get("/v1/pairs/EUR/XAU").json()
    assert body["previous"] is None
    assert body["change_abs"] is None and body["change_pct"] is None
    assert body["stats"] == {"min": "0.00029946", "max": "0.00029946", "avg": "0.00029946"}


@pytest.mark.parametrize("path", ["/v1/pairs/USD/GBP", "/v1/pairs/EUR/BTC", "/v1/pairs/EUR/XYZ",
                                  "/v1/pairs/EUR/EUR"])
def test_no_data_is_404(seeded, path):
    resp = seeded.get(path)
    assert resp.status_code == 404
    assert resp.json()["type"] == "/problems/not-found"


@pytest.mark.parametrize(
    ("path", "field"),
    [("/v1/pairs/eur/USD", "base"), ("/v1/pairs/EUR/U", "quote"),
     ("/v1/pairs/EUR/USD?window=2w", "window")],
)
def test_bad_params_400(seeded, path, field):
    resp = seeded.get(path)
    assert resp.status_code == 400
    assert [e["field"] for e in resp.json()["errors"]] == [field]


@pytest.mark.parametrize(
    ("query", "interval", "series_dates"),
    [
        ("window=7d", "day", ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17"]),
        ("window=1y", "day", ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17"]),
        ("window=3y", "week", ["2026-11-15", "2026-11-17"]),  # last rate of each ISO week
        ("window=5y", "week", ["2026-11-15", "2026-11-17"]),
        ("window=max", "month", ["2026-11-17"]),  # last rate of November
        ("window=7d&interval=month", "month", ["2026-11-17"]),  # explicit override
        ("window=max&interval=day", "day", ["2026-11-13", "2026-11-14", "2026-11-15",
                                            "2026-11-16", "2026-11-17"]),
    ],
)
def test_interval_defaults_and_override(seeded, query, interval, series_dates):
    body = seeded.get(f"/v1/pairs/EUR/USD?{query}").json()
    assert body["series_interval"] == interval
    assert [p["date"] for p in body["series"]] == series_dates
    assert body["data_points"] == 5  # stats and trend always use every daily rate
    assert body["series"][-1]["rate"] == body["latest"]["rate"]


def test_window_longer_than_history_returns_what_exists(seeded):
    """5 years asked for, 5 days stored: 200, and period_start shows the real start."""
    resp = seeded.get("/v1/pairs/EUR/USD?window=5y")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_start"] == "2021-11-19"  # 2026-11-17 minus 1824 days
    assert body["trend"]["period_start"] == body["first_available_date"] == "2026-11-13"
    assert body["trend"]["period_start"] > body["window_start"]  # = partial history
    assert body["trend"]["annualized_change_pct"] is None  # only 4 days of data


def test_bad_interval_is_400(seeded):
    resp = seeded.get("/v1/pairs/EUR/USD?interval=year")
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["field"] == "interval"
