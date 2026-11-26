"""Importer tests: real test database, fake API (respx)."""

import datetime as dt

import httpx
import pytest
import respx

from fx_explorer.__main__ import build_parser, main
from fx_explorer.frankfurter.client import ApiError, FrankfurterClient
from fx_explorer.importer import Importer, NothingToResumeError
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.import_runs import ImportRunRepository
from fx_explorer.repositories.rates import RateRepository

BASE = "https://api.test/v2"
CURRENCIES = [
    {"iso_code": c, "name": c, "type": "currency", "iso_numeric": None, "symbol": None,
     "start_date": "1999-01-04", "end_date": "2026-11-20"}
    for c in ("EUR", "USD", "GBP")
]


def rates_json(*items):
    return [{"date": d, "base": "EUR", "quote": q, "rate": r} for d, q, r in items]


GOOD = rates_json(
    ("2026-11-19", "USD", 1.1), ("2026-11-19", "GBP", 0.84),
    ("2026-11-20", "USD", 1.0989), ("2026-11-20", "GBP", 0.83641),
)


@pytest.fixture
def api():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/currencies").mock(return_value=httpx.Response(200, json=CURRENCIES))
        yield mock


@pytest.fixture
def importer(pool, api):
    client = FrankfurterClient(BASE, request_delay_seconds=0, sleep=lambda s: None)
    yield Importer(client, CurrencyRepository(pool), RateRepository(pool), ImportRunRepository(pool))
    client.close()


pytestmark = pytest.mark.integration


def test_import_then_repeat_is_idempotent(importer, api, pool):
    api.get("/rates").mock(return_value=httpx.Response(200, json=GOOD))

    first = importer.run("EUR", ["USD", "GBP"], start=dt.date(2026, 11, 19), end=dt.date(2026, 11, 20))
    assert (first.status, first.fetched, first.inserted, first.updated, first.rejected) == ("success", 4, 4, 0, 0)

    second = importer.run("EUR", ["USD", "GBP"], start=dt.date(2026, 11, 19), end=dt.date(2026, 11, 20))
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 4)
    assert RateRepository(pool).count() == 4

    row = ImportRunRepository(pool).get(second.run_id)
    assert row["status"] == "success" and row["finished_at"] is not None and row["fetched"] == 4


def test_revised_rate_counts_as_update(importer, api):
    route = api.get("/rates")
    route.mock(return_value=httpx.Response(200, json=GOOD))
    importer.run("EUR", ["USD", "GBP"], start=dt.date(2026, 11, 19), end=dt.date(2026, 11, 20))
    revised = [dict(r) for r in GOOD]
    revised[2]["rate"] = 1.1
    route.mock(return_value=httpx.Response(200, json=revised))
    result = importer.run("EUR", ["USD", "GBP"], start=dt.date(2026, 11, 19), end=dt.date(2026, 11, 20))
    assert (result.inserted, result.updated, result.unchanged) == (0, 1, 3)


def test_self_pair_and_bad_records_give_partial(importer, api, pool):
    data = GOOD + rates_json(
        ("2026-11-20", "EUR", 1.0),  # self-pair: skipped, not a reject
        ("2026-11-20", "USD", -5),  # bad rate
        ("2026-11-20", "ZZZ", 3),  # unknown currency
    )
    api.get("/rates").mock(return_value=httpx.Response(200, json=data))
    result = importer.run("EUR", ["USD", "GBP"])
    assert result.status == "partial"
    assert (result.fetched, result.inserted, result.rejected) == (7, 4, 2)
    assert ImportRunRepository(pool).get(result.run_id)["status"] == "partial"


def test_api_error_marks_run_failed(importer, api, pool):
    api.get("/rates").mock(
        return_value=httpx.Response(422, json={"status": 422, "message": "invalid currency: XXX"})
    )
    with pytest.raises(ApiError):
        importer.run("XXX", ["USD"])
    row = ImportRunRepository(pool).get(1)
    assert row["status"] == "failed"
    assert "invalid currency: XXX" in row["error_message"]


def test_incremental_goes_back_three_days(importer, api):
    route = api.get("/rates")
    route.mock(return_value=httpx.Response(200, json=GOOD))
    importer.run("EUR", ["USD", "GBP"], start=dt.date(2026, 11, 19), end=dt.date(2026, 11, 20))

    importer.run_incremental("EUR", ["USD", "GBP"], today=dt.date(2026, 11, 21))

    params = dict(route.calls.last.request.url.params)
    assert params["from"] == "2026-11-17"  # newest stored (11-20) minus 3 days
    assert params["to"] == "2026-11-21"


def test_incremental_without_data_explains_itself(importer):
    with pytest.raises(NothingToResumeError):
        importer.run_incremental("EUR", ["USD"])


# --- command line parsing (no database needed) ---------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["import", "--incremental", "--date", "2026-11-20"],
        ["import", "--date", "2026-11-20", "--from", "2026-11-01"],
        ["import", "--to", "2026-11-20"],
        ["import", "--from", "2026-11-20", "--to", "2026-11-01"],
        ["import", "--date", "20-11-2026"],
    ],
)
def test_bad_cli_combinations_are_rejected(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_cli_parses_options():
    args = build_parser().parse_args(
        ["import", "--base", "EUR", "--quotes", "usd, gbp", "--from", "2026-11-01", "--to", "2026-11-20"]
    )
    assert args.quotes == ["USD", "GBP"]
    assert args.start == dt.date(2026, 11, 1) and args.end == dt.date(2026, 11, 20)


def test_records_outside_requested_range_are_not_stored(importer, api, pool):
    """Frankfurter adds the last rate BEFORE `from` when a currency has none on day one
    (seen for NGN: asking for 2017 also returns 2016-12-30). We must not store it."""
    data = rates_json(("2016-12-30", "USD", 1.0541)) + GOOD
    api.get("/rates").mock(return_value=httpx.Response(200, json=data))
    result = importer.run("EUR", ["USD", "GBP"], start=dt.date(2026, 11, 19), end=dt.date(2026, 11, 20))
    assert (result.status, result.fetched, result.inserted, result.rejected) == ("success", 5, 4, 0)
    assert RateRepository(pool).count() == 4
