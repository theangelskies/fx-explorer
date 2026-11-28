"""GET /v1/movers and GET /v1/import-runs/latest (ADR 0007).

Seed data: EUR/USD 13–17 Nov ending 1.1034 -> 1.1005; EUR/GBP flat at 0.83641; EUR/XAU one rate.
"""

from datetime import date
from decimal import Decimal

import pytest

from fx_explorer.domain.models import ExchangeRate
from fx_explorer.repositories.import_runs import ImportRunRepository
from fx_explorer.repositories.rates import RateRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def movers_data(seeded, pool):
    # GBP gets a newer day than USD, like NGN often lagging a day behind in real data.
    RateRepository(pool).upsert_many([ExchangeRate(
        rate_date=date(2026, 11, 18), base="EUR", quote="GBP", rate=Decimal("0.84"))])
    return seeded


# ------------------------------------------------------------------- movers

def test_movers_biggest_first_with_per_quote_dates(movers_data):
    body = movers_data.get("/v1/movers").json()
    assert body["base"] == "EUR"
    assert body["data"] == [
        # (0.84 / 0.83641 − 1) × 100 = 0.42921… → 0.4292
        {"quote": "GBP", "rate_date": "2026-11-18", "rate": "0.84",
         "previous_rate_date": "2026-11-17", "previous_rate": "0.83641",
         "change_abs": "0.00359", "change_pct": "0.4292"},
        # (1.1005 / 1.1034 − 1) × 100 = −0.26282… → −0.2628
        {"quote": "USD", "rate_date": "2026-11-17", "rate": "1.1005",
         "previous_rate_date": "2026-11-16", "previous_rate": "1.1034",
         "change_abs": "-0.0029", "change_pct": "-0.2628"},
    ]  # XAU has only one stored rate, so it has no move and is left out


def test_movers_ranked_by_size_not_sign(seeded):
    body = seeded.get("/v1/movers").json()
    # USD fell 0.26 %, GBP didn't move: the fall is the bigger move
    assert [m["quote"] for m in body["data"]] == ["USD", "GBP"]
    assert body["data"][1]["change_pct"] == "0.0000"


@pytest.mark.parametrize(("query", "quotes"),
                         [("direction=up", ["GBP"]), ("direction=down", ["USD"]), ("limit=1", ["GBP"])])
def test_movers_direction_and_limit(movers_data, query, quotes):
    assert [m["quote"] for m in movers_data.get(f"/v1/movers?{query}").json()["data"]] == quotes


def test_movers_known_base_without_rates_is_empty(seeded):
    assert seeded.get("/v1/movers?base=USD").json() == {"base": "USD", "data": []}


@pytest.mark.parametrize(("query", "field"),
                         [("base=XYZ", "base"), ("base=eur", "base"), ("limit=0", "limit"),
                          ("limit=51", "limit"), ("direction=sideways", "direction")])
def test_movers_bad_params_400(seeded, query, field):
    resp = seeded.get(f"/v1/movers?{query}")
    assert resp.status_code == 400
    assert [e["field"] for e in resp.json()["errors"]] == [field]


# --------------------------------------------------------------- import runs

def test_no_import_yet_is_404(client):
    resp = client.get("/v1/import-runs/latest")
    assert resp.status_code == 404
    assert resp.json()["type"] == "/problems/not-found"


def test_latest_run_is_the_most_recently_started_any_status(client, pool):
    runs = ImportRunRepository(pool)
    ok = runs.start({"base": "EUR"})
    runs.finish(ok, "success", fetched=10, inserted=4, updated=1)
    failed = runs.start({"base": "EUR"})
    runs.finish(failed, "failed", error_message="RetriesExhaustedError: GET /rates failed")

    body = client.get("/v1/import-runs/latest").json()
    assert body["id"] == failed and body["status"] == "failed"  # failures aren't hidden
    assert body["error_message"] == "RetriesExhaustedError: GET /rates failed"
    assert body["started_at"].endswith("Z") and body["finished_at"].endswith("Z")
    assert "params" not in body  # internal detail, not in the contract


def test_running_import_has_no_finish_time(client, pool):
    run_id = ImportRunRepository(pool).start({"base": "EUR"})
    body = client.get("/v1/import-runs/latest").json()
    assert body == {**body, "id": run_id, "status": "running", "finished_at": None,
                    "fetched": 0, "inserted": 0, "updated": 0, "rejected": 0, "error_message": None}
