from datetime import date
from decimal import Decimal

from fx_explorer.parsing.currencies import parse_currencies
from fx_explorer.parsing.rates import parse_rates

KNOWN = {"EUR", "USD", "GBP", "NGN"}


def rec(**overrides):
    record = {"date": "2026-11-20", "base": "EUR", "quote": "USD", "rate": Decimal("1.0989")}
    record.update(overrides)
    return record


def test_valid_record():
    valid, rejects = parse_rates([rec()], KNOWN)
    assert rejects == []
    assert len(valid) == 1
    r = valid[0]
    assert (r.rate_date, r.base, r.quote, r.rate) == (date(2026, 11, 20), "EUR", "USD", Decimal("1.0989"))
    assert isinstance(r.rate_date, date) and isinstance(r.rate, Decimal)


def test_missing_field_is_rejected():
    raw = {"date": "2026-11-20", "base": "EUR", "quote": "USD"}  # no rate
    valid, rejects = parse_rates([raw], KNOWN)
    assert valid == []
    assert rejects[0].raw == raw
    assert "missing field: rate" in rejects[0].reason


def test_null_rate_is_rejected():
    valid, rejects = parse_rates([rec(rate=None)], KNOWN)
    assert valid == [] and len(rejects) == 1


def test_negative_and_zero_rates_rejected():
    valid, rejects = parse_rates([rec(rate=Decimal("-1.5")), rec(rate=Decimal("0"))], KNOWN)
    assert valid == []
    assert all("greater than 0" in r.reason for r in rejects)


def test_nan_rate_rejected():
    valid, rejects = parse_rates([rec(rate=Decimal("NaN"))], KNOWN)
    assert valid == [] and "finite" in rejects[0].reason


def test_bad_dates_rejected():
    valid, rejects = parse_rates(
        [rec(date="2026-13-45"), rec(date="20-11-2026"), rec(date="not a date")], KNOWN
    )
    assert valid == []
    assert len(rejects) == 3 and all("invalid date" in r.reason for r in rejects)


def test_codes_are_trimmed_and_uppercased():
    valid, rejects = parse_rates([rec(base=" eur ", quote="usd")], KNOWN)
    assert rejects == []
    assert (valid[0].base, valid[0].quote) == ("EUR", "USD")


def test_self_pair_is_skipped_without_being_a_reject():
    valid, rejects = parse_rates([rec(quote="EUR", rate=Decimal("1.0")), rec(quote=" eur")], KNOWN)
    assert valid == [] and rejects == []


def test_unknown_currency_rejected():
    valid, rejects = parse_rates([rec(quote="ZZZ")], KNOWN)
    assert valid == []
    assert "unknown currency code: ZZZ" in rejects[0].reason


def test_unknown_check_can_be_turned_off():
    valid, _ = parse_rates([rec(quote="ZZZ")], known_codes=None)
    assert len(valid) == 1


def test_not_a_dict_is_rejected_not_raised():
    valid, rejects = parse_rates(["oops", 42, None], KNOWN)
    assert valid == [] and len(rejects) == 3


def test_one_bad_record_among_good_ones():
    records = [
        rec(quote="USD"),
        rec(quote="GBP", rate=Decimal("0.83641")),
        rec(quote="NGN", rate=Decimal("-3")),  # the bad one
        rec(quote="EUR", rate=Decimal("1")),  # self-pair
        rec(quote="NGN", rate=Decimal("1512.17")),
    ]
    valid, rejects = parse_rates(records, KNOWN)
    assert [r.quote for r in valid] == ["USD", "GBP", "NGN"]
    assert len(rejects) == 1 and rejects[0].raw is records[2]


def test_parse_currencies_valid_and_bad():
    raw = [
        {"iso_code": "usd ", "iso_numeric": "840", "name": "US Dollar", "symbol": "$",
         "type": "currency", "start_date": "1948-06-21", "end_date": "2026-11-20"},
        {"iso_code": "AAVE", "iso_numeric": None, "name": "Aave", "symbol": "AAVE",
         "type": "crypto", "start_date": None, "end_date": None},
        {"iso_code": "BAD", "name": "Bad", "type": "spaceship"},
        {"name": "No code", "type": "currency"},
    ]
    valid, rejects = parse_currencies(raw)
    assert [c.iso_code for c in valid] == ["USD", "AAVE"]
    assert valid[0].start_date == date(1948, 6, 21) and valid[1].start_date is None
    assert len(rejects) == 2
