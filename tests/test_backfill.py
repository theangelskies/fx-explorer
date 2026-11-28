"""Backfill: yearly chunks, start_date clamping, failure isolation, idempotency. No network."""

import datetime as dt

import httpx
import pytest
import respx

from fx_explorer.__main__ import main
from fx_explorer.backfill import PlannedRequest, plan_backfill, year_chunks
from fx_explorer.domain.models import Currency
from fx_explorer.frankfurter.client import FrankfurterClient
from fx_explorer.importer import Importer
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.import_runs import ImportRunRepository
from fx_explorer.repositories.rates import RateRepository

D = dt.date

# ------------------------------------------------------------- pure planning

def test_year_chunks_with_partial_first_and_current_year():
    assert year_chunks(D(2016, 3, 15), D(2018, 11, 26)) == [
        (2016, D(2016, 3, 15), D(2016, 12, 31)),
        (2017, D(2017, 1, 1), D(2017, 12, 31)),
        (2018, D(2018, 1, 1), D(2018, 11, 26)),  # partial "current" year
    ]


def test_year_chunks_single_day_and_bad_range():
    assert year_chunks(D(2020, 2, 29), D(2020, 2, 29)) == [(2020, D(2020, 2, 29), D(2020, 2, 29))]
    with pytest.raises(ValueError):
        year_chunks(D(2020, 1, 2), D(2020, 1, 1))


STARTS = {"EUR": D(1999, 1, 1), "USD": D(1948, 6, 21), "GBP": D(1949, 12, 21),
          "NEW": D(2017, 5, 2), "LATE": D(2030, 1, 1), "NOSTART": None}


def test_plan_one_request_per_year_when_nothing_is_limited():
    plan = plan_backfill("EUR", ["USD", "GBP"], D(2016, 1, 1), D(2018, 6, 30), STARTS)
    assert plan.requests == [
        PlannedRequest(2016, D(2016, 1, 1), D(2016, 12, 31), ("USD", "GBP")),
        PlannedRequest(2017, D(2017, 1, 1), D(2017, 12, 31), ("USD", "GBP")),
        PlannedRequest(2018, D(2018, 1, 1), D(2018, 6, 30), ("USD", "GBP")),
    ]
    assert plan.limits == []


def test_plan_clamps_a_quote_that_starts_later():
    plan = plan_backfill("EUR", ["USD", "NEW"], D(2016, 1, 1), D(2018, 12, 31), STARTS)
    assert plan.requests == [
        PlannedRequest(2016, D(2016, 1, 1), D(2016, 12, 31), ("USD",)),  # NEW doesn't exist yet
        PlannedRequest(2017, D(2017, 1, 1), D(2017, 12, 31), ("USD",)),
        PlannedRequest(2017, D(2017, 5, 2), D(2017, 12, 31), ("NEW",)),  # its own start
        PlannedRequest(2018, D(2018, 1, 1), D(2018, 12, 31), ("USD", "NEW")),
    ]
    assert plan.limits == ["NEW limited: data starts 2017-05-02 (its start_date), not 2016-01-01."]


def test_plan_never_requests_before_any_start_date():
    plan = plan_backfill("EUR", ["USD", "GBP", "NEW"], D(1940, 1, 1), D(2026, 11, 26), STARTS)
    for req in plan.requests:
        for q in req.quotes:
            assert req.start >= STARTS[q] and req.start >= STARTS["EUR"]
    assert plan.requests[0].start == D(1999, 1, 1)  # the base (EUR) starts in 1999
    assert "EUR (base) has data only from 1999-01-01: every quote starts there." in plan.limits


def test_plan_skips_unknown_late_and_self_quotes():
    plan = plan_backfill("EUR", ["USD", "LATE", "XYZ", "EUR", "NOSTART"],
                         D(2020, 1, 1), D(2020, 12, 31), STARTS)
    assert plan.requests == [PlannedRequest(2020, D(2020, 1, 1), D(2020, 12, 31), ("USD", "NOSTART"))]
    assert plan.limits == [
        "LATE skipped: data starts 2030-01-01, after the range end 2020-12-31.",
        "XYZ skipped: not in the currencies table (unknown currency).",
        "EUR skipped: it is the base currency.",
        "NOSTART: start_date unknown, requesting from 2020-01-01 unchanged.",
    ]


def test_plan_with_unknown_base_has_no_requests():
    plan = plan_backfill("ZZZ", ["USD"], D(2020, 1, 1), D(2020, 12, 31), STARTS)
    assert plan.requests == [] and "not in the currencies table" in plan.limits[0]


# --------------------------------------------------- running it (real test DB, fake API)

BASE_URL = "https://api.test/v2"
CURRENCIES = [
    {"iso_code": "EUR", "name": "Euro", "type": "currency", "iso_numeric": "978", "symbol": "€",
     "start_date": "1999-01-01", "end_date": "2026-11-20"},
    {"iso_code": "USD", "name": "US Dollar", "type": "currency", "iso_numeric": "840", "symbol": "$",
     "start_date": "1948-06-21", "end_date": "2026-11-20"},
    {"iso_code": "NEW", "name": "New coin", "type": "crypto", "iso_numeric": None, "symbol": None,
     "start_date": "2017-07-01", "end_date": "2026-11-20"},
]


class FakeRatesApi:
    """Answers GET /rates with one rate per day per quote, and remembers every request."""

    def __init__(self, fail_years: set[int] = frozenset()):
        self.fail_years = fail_years
        self.requests: list[tuple[str, D, D]] = []  # (quotes, from, to)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        p = request.url.params
        start, end = D.fromisoformat(p["from"]), D.fromisoformat(p["to"])
        self.requests.append((p["quotes"], start, end))
        if start.year in self.fail_years:
            return httpx.Response(503)
        rows, day = [], start
        while day <= end:
            for q in p["quotes"].split(","):
                rows.append({"date": day.isoformat(), "base": p["base"], "quote": q,
                             "rate": round(1 + day.toordinal() % 97 / 1000, 4)})
            day += dt.timedelta(days=1)
        return httpx.Response(200, json=rows)


@pytest.fixture
def make_importer(pool):
    clients = []

    def make(fake: FakeRatesApi, mock):
        mock.get("/currencies").mock(return_value=httpx.Response(200, json=CURRENCIES))
        mock.get("/rates").mock(side_effect=fake)
        client = FrankfurterClient(BASE_URL, request_delay_seconds=0, max_retries=2,
                                   sleep=lambda s: None)
        clients.append(client)
        return Importer(client, CurrencyRepository(pool), RateRepository(pool),
                        ImportRunRepository(pool))

    yield make
    for c in clients:
        c.close()


@pytest.mark.integration
def test_backfill_imports_each_year_and_rerun_inserts_nothing(make_importer, pool):
    fake = FakeRatesApi()
    with respx.mock(base_url=BASE_URL) as mock:
        importer = make_importer(fake, mock)
        first = importer.run_backfill("EUR", ["USD", "NEW"], D(2016, 1, 1), D(2018, 3, 31))

        assert first.failed_years == []
        assert [(c.year, c.start, c.quotes) for c in first.chunks] == [
            (2016, D(2016, 1, 1), ("USD",)),
            (2017, D(2017, 1, 1), ("USD",)),
            (2017, D(2017, 7, 1), ("NEW",)),
            (2018, D(2018, 1, 1), ("USD", "NEW")),
        ]
        # never asked for NEW before its start_date
        assert all(start >= D(2017, 7, 1) for quotes, start, _ in fake.requests if "NEW" in quotes)
        usd_days = (D(2018, 3, 31) - D(2016, 1, 1)).days + 1
        new_days = (D(2018, 3, 31) - D(2017, 7, 1)).days + 1
        assert first.inserted == usd_days + new_days == RateRepository(pool).count()

        # one import_runs row per request, all successful
        runs = [ImportRunRepository(pool).get(c.summary.run_id) for c in first.chunks]
        assert [r["status"] for r in runs] == ["success"] * 4

        second = importer.run_backfill("EUR", ["USD", "NEW"], D(2016, 1, 1), D(2018, 3, 31))
        assert (second.inserted, second.updated) == (0, 0)
        assert second.unchanged == first.inserted
        assert RateRepository(pool).count() == first.inserted  # no duplicates


@pytest.mark.integration
def test_one_failing_year_does_not_stop_the_others(make_importer, pool):
    fake = FakeRatesApi(fail_years={2017})
    with respx.mock(base_url=BASE_URL) as mock:
        summary = make_importer(fake, mock).run_backfill("EUR", ["USD"], D(2016, 1, 1), D(2018, 12, 31))

    assert summary.failed_years == [2017]
    by_year = {c.year: c for c in summary.chunks}
    assert by_year[2016].summary.inserted == 366 and by_year[2018].summary.inserted == 365
    assert "failed after 3 attempts" in by_year[2017].error  # 1 try + 2 retries
    # the failure is recorded in import_runs too
    with pool.connection() as conn:
        statuses = [r[0] for r in conn.execute("SELECT status FROM import_runs ORDER BY id")]
    assert statuses == ["success", "failed", "success"]


@pytest.mark.integration
def test_cli_dry_run_prints_plan_and_calls_nothing(pool, capsys, monkeypatch):
    CurrencyRepository(pool).upsert_many([
        Currency(iso_code="EUR", name="Euro", type="currency", start_date=D(1999, 1, 1)),
        Currency(iso_code="USD", name="US Dollar", type="currency", start_date=D(1948, 6, 21)),
        Currency(iso_code="NEW", name="New coin", type="crypto", start_date=D(2017, 7, 1)),
    ])
    monkeypatch.setattr("fx_explorer.__main__.create_pool", lambda: _NoClose(pool))

    def no_client(*a, **k):
        raise AssertionError("dry run must not create an HTTP client")
    monkeypatch.setattr("fx_explorer.__main__.FrankfurterClient.from_settings", no_client)

    with respx.mock(assert_all_called=False) as mock:  # any real request would fail loudly
        code = main(["import", "--backfill", "--base", "EUR", "--quotes", "USD,NEW",
                     "--from", "2016-01-01", "--to", "2017-12-31", "--dry-run"])
        assert not mock.calls
    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines() == [
        "Backfill plan: base EUR, 2016-01-01 .. 2017-12-31",
        "  note: NEW limited: data starts 2017-07-01 (its start_date), not 2016-01-01.",
        "  2016  GET /rates?base=EUR&quotes=USD&from=2016-01-01&to=2016-12-31",
        "  2017  GET /rates?base=EUR&quotes=USD&from=2017-01-01&to=2017-12-31",
        "  2017  GET /rates?base=EUR&quotes=NEW&from=2017-07-01&to=2017-12-31",
        "3 requests over 2 years.",
        "Dry run: no API calls made, nothing written.",
    ]
    assert RateRepository(pool).count() == 0


class _NoClose:
    """Wraps the test pool so main() can 'close' it without closing the fixture's pool."""

    def __init__(self, pool):
        self._pool = pool

    def __getattr__(self, name):
        return getattr(self._pool, name)

    def close(self):
        pass


@pytest.mark.parametrize(
    "argv",
    [
        ["import", "--backfill"],                                  # needs --from
        ["import", "--backfill", "--from", "2016-01-01", "--incremental"],
        ["import", "--backfill", "--date", "2016-01-01"],
        ["import", "--from", "2016-01-01", "--dry-run"],           # dry-run needs --backfill
    ],
)
def test_bad_backfill_cli_combinations(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
