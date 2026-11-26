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
