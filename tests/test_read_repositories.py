"""Integration tests for the API's read queries: currencies, rates (keyset pages), pair window."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from fx_explorer.domain.models import Currency, ExchangeRate
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.rates import RateRepository, RatePoint

pytestmark = pytest.mark.integration

# Real EUR -> USD values from docs/samples/c_weekend_range.json
EUR_USD = {
    date(2026, 11, 13): "1.1076",
    date(2026, 11, 14): "1.1072",
    date(2026, 11, 15): "1.1068",
    date(2026, 11, 16): "1.1034",
    date(2026, 11, 17): "1.1005",
}


@pytest.fixture
def data(pool):
    currencies = CurrencyRepository(pool)
    currencies.upsert_many([
        Currency(iso_code="EUR", name="Euro", type="currency", iso_numeric="978", symbol="€"),
        Currency(iso_code="USD", name="United States Dollar", type="currency", iso_numeric="840"),
        Currency(iso_code="GBP", name="British Pound", type="currency", iso_numeric="826"),
        Currency(iso_code="XAU", name="Gold (Troy Ounce)", type="metal"),
        Currency(iso_code="BTC", name="Bitcoin", type="crypto"),
        Currency(iso_code="USDT", name="Tether_USD 100%", type="crypto"),
    ])
    rates = RateRepository(pool)
    batch = [ExchangeRate(rate_date=d, base="EUR", quote="USD", rate=Decimal(v))
             for d, v in EUR_USD.items()]
    batch += [ExchangeRate(rate_date=d, base="EUR", quote="GBP", rate=Decimal("0.83641"))
              for d in EUR_USD]
    batch.append(ExchangeRate(rate_date=date(2026, 11, 17), base="EUR", quote="XAU",
                              rate=Decimal("0.00029946")))
    rates.upsert_many(batch)
    return currencies, rates


# ---------------------------------------------------------------- currencies

def test_currency_summary_counts_both_sides(data):
    currencies, _ = data
    eur = currencies.get("EUR")
    assert eur.rates_count == 11  # base of 5 USD + 5 GBP + 1 XAU
    assert eur.latest_rate_date == date(2026, 11, 17)
    usd = currencies.get("USD")
    assert (usd.rates_count, usd.latest_rate_date, usd.iso_numeric) == (5, date(2026, 11, 17), "840")
    btc = currencies.get("BTC")
    assert (btc.rates_count, btc.latest_rate_date, btc.iso_numeric) == (0, None, None)
    assert currencies.get("XYZ") is None


def test_currency_filters(data):
    currencies, _ = data
    codes = lambda page: [c.iso_code for c in page.items]  # noqa: E731
    assert codes(currencies.list_page(limit=50)) == ["BTC", "EUR", "GBP", "USD", "USDT", "XAU"]
    assert codes(currencies.list_page(limit=50, type_="crypto")) == ["BTC", "USDT"]
    assert codes(currencies.list_page(limit=50, q="dollar")) == ["USD"]  # name, any case
    assert codes(currencies.list_page(limit=50, q="usd")) == ["USD", "USDT"]  # code
    assert codes(currencies.list_page(limit=50, has_rates=True)) == ["EUR", "GBP", "USD", "XAU"]
    assert codes(currencies.list_page(limit=50, has_rates=False)) == ["BTC", "USDT"]
    assert codes(currencies.list_page(limit=50, type_="metal", has_rates=False)) == []


def test_currency_search_wildcards_are_literal(data):
    currencies, _ = data
    # "%" and "_" must match those characters, not act as ILIKE wildcards
    assert [c.iso_code for c in currencies.list_page(limit=50, q="%").items] == ["USDT"]
    assert [c.iso_code for c in currencies.list_page(limit=50, q="_").items] == ["USDT"]


def test_currency_pages_cover_everything_once(data):
    currencies, _ = data
    seen, after = [], None
    while True:
        page = currencies.list_page(limit=4, after_code=after)
        seen += [c.iso_code for c in page.items]
        if not page.has_more:
            break
        after = page.items[-1].iso_code
    assert seen == ["BTC", "EUR", "GBP", "USD", "USDT", "XAU"]


def test_find_known(data):
    currencies, _ = data
    assert currencies.find_known(["USD", "XYZ", "EUR"]) == {"USD", "EUR"}
    assert currencies.find_known([]) == set()


# --------------------------------------------------------------------- rates

def all_pages(rates: RateRepository, limit: int, **filters):
    """Follow the cursor from the first page to the last, like an API client would."""
    rows, after, pages = [], None, 0
    while True:
        page = rates.list_page(base="EUR", limit=limit, after=after, **filters)
        pages += 1
        assert len(page.items) <= limit
        rows += page.items
        if not page.has_more:
            return rows, pages
        after = (page.items[-1].rate_date, page.items[-1].quote)


@pytest.mark.parametrize("limit", [1, 2, 3, 50])
def test_rate_pages_newest_first_no_duplicates_no_gaps(data, limit):
    _, rates = data
    rows, pages = all_pages(rates, limit)
    keys = [(r.rate_date, r.quote) for r in rows]
    assert len(keys) == 11 and len(set(keys)) == 11  # every row exactly once
    # date descending, then quote A->Z within one date
    assert keys == sorted(keys, key=lambda k: (-k[0].toordinal(), k[1]))
    assert keys[:3] == [(date(2026, 11, 17), "GBP"), (date(2026, 11, 17), "USD"),
                        (date(2026, 11, 17), "XAU")]
    assert pages == -(-11 // limit)  # ceil: no empty trailing page


def test_rate_pages_oldest_first(data):
    _, rates = data
    rows, _ = all_pages(rates, 2, newest_first=False)
    keys = [(r.rate_date, r.quote) for r in rows]
    assert keys == sorted(keys) and len(keys) == 11


def test_rate_filters(data):
    _, rates = data
    rows, _ = all_pages(rates, 50, quotes=["USD"], date_from=date(2026, 11, 14),
                        date_to=date(2026, 11, 16))
    assert [(r.rate_date, r.rate) for r in rows] == [
        (date(2026, 11, 16), Decimal("1.1034")),
        (date(2026, 11, 15), Decimal("1.1068")),
        (date(2026, 11, 14), Decimal("1.1072")),
    ]
    assert rows[0].updated_at is not None


def test_rate_filter_with_no_matches_is_empty_not_error(data):
    _, rates = data
    page = rates.list_page(base="EUR", limit=50, date_from=date(2030, 1, 1))
    assert page.items == [] and page.has_more is False


def test_rate_values_keep_exact_precision(data):
    _, rates = data
    page = rates.list_page(base="EUR", limit=50, quotes=["XAU"])
    assert str(page.items[0].rate) == "0.00029946"


def test_new_rows_during_paging_do_not_cause_duplicates(data):
    """The reason for cursors (ADR 0003): an import adds a newer day while a client pages."""
    _, rates = data
    first = rates.list_page(base="EUR", limit=3)
    rates.upsert_many([ExchangeRate(rate_date=date(2026, 11, 18), base="EUR", quote="USD",
                                    rate=Decimal("1.1000"))])
    last = first.items[-1]
    rest = rates.list_page(base="EUR", limit=50, after=(last.rate_date, last.quote))
    first_keys = {(r.rate_date, r.quote) for r in first.items}
    rest_keys = {(r.rate_date, r.quote) for r in rest.items}
    assert first_keys.isdisjoint(rest_keys)
    assert len(first_keys) + len(rest_keys) == 11  # the old rows, each seen once


# --------------------------------------------------------------------- pairs

def test_pair_history(data):
    _, rates = data
    h = rates.pair_history("EUR", "USD", days=7)
    assert h.latest == RatePoint(date(2026, 11, 17), Decimal("1.1005"))
    assert h.previous == RatePoint(date(2026, 11, 16), Decimal("1.1034"))
    assert h.first_available_date == date(2026, 11, 13)
    assert h.window_start == date(2026, 11, 11)  # 17th minus 6 days: a 7-day window
    assert [p.rate_date for p in h.points] == sorted(EUR_USD)  # oldest first
    assert h.series == h.points  # interval "day"
    assert h.recent == h.points  # fewer than 200 stored, so all of them


def test_pair_history_window_ends_at_latest_stored_date(data):
    _, rates = data
    h = rates.pair_history("EUR", "USD", days=2)  # 16th and 17th only
    assert [p.rate_date for p in h.points] == [date(2026, 11, 16), date(2026, 11, 17)]
    assert len(h.recent) == 5  # moving averages may look before the window


def test_pair_history_max_starts_at_first_available(data):
    _, rates = data
    h = rates.pair_history("EUR", "USD", days=None)
    assert h.window_start == h.first_available_date == date(2026, 11, 13)
    assert len(h.points) == 5


@pytest.mark.parametrize(
    ("interval", "expected"),
    [
        # 13 (Fri), 14 (Sat), 15 (Sun) are in one ISO week, 16 (Mon) and 17 (Tue) in the next:
        # the last rate of each week is kept.
        ("week", [(date(2026, 11, 15), Decimal("1.1068")), (date(2026, 11, 17), Decimal("1.1005"))]),
        ("month", [(date(2026, 11, 17), Decimal("1.1005"))]),  # all in November
    ],
)
def test_pair_history_downsampling_keeps_last_rate_per_period(data, interval, expected):
    _, rates = data
    h = rates.pair_history("EUR", "USD", days=None, interval=interval)
    assert [(p.rate_date, p.rate) for p in h.series] == expected
    assert len(h.points) == 5  # stats/trend still get every daily rate


def test_pair_history_rejects_unknown_interval(data):
    _, rates = data
    with pytest.raises(ValueError):
        rates.pair_history("EUR", "USD", days=7, interval="year")


def test_pair_without_data(data):
    _, rates = data
    assert rates.pair_history("USD", "GBP", days=30) is None  # no cross rates
    assert rates.has_pair("EUR", "USD") is True
    assert rates.has_pair("USD", "EUR") is False
