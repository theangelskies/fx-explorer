"""Integration tests: real PostgreSQL (the test database), no mocks."""

import logging
from datetime import date
from decimal import Decimal

import pytest

from fx_explorer.domain.models import Currency, ExchangeRate
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.import_runs import ImportRunRepository
from fx_explorer.repositories.rates import RateRepository

pytestmark = pytest.mark.integration

D = date(2026, 11, 20)


def cur(code: str, name: str = "x", type_: str = "currency") -> Currency:
    return Currency(iso_code=code, name=name, type=type_)


def rate(quote: str, value: str, d: date = D) -> ExchangeRate:
    return ExchangeRate(rate_date=d, base="EUR", quote=quote, rate=Decimal(value))


@pytest.fixture
def repos(pool):
    currencies = CurrencyRepository(pool)
    currencies.upsert_many([cur("EUR"), cur("USD"), cur("GBP")])
    return RateRepository(pool), currencies


def test_currencies_insert_then_update_then_unchanged(pool):
    repo = CurrencyRepository(pool)
    assert repo.upsert_many([cur("EUR", "Euro"), cur("USD", "Dollar")]).inserted == 2
    again = repo.upsert_many([cur("EUR", "Euro"), cur("USD", "Dollar")])
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 2)
    changed = repo.upsert_many([cur("USD", "US Dollar")])
    assert (changed.inserted, changed.updated) == (0, 1)
    assert repo.known_codes() == {"EUR", "USD"}


def test_insert_rates(repos):
    rates, _ = repos
    result = rates.upsert_many([rate("USD", "1.0989"), rate("GBP", "0.83641")])
    assert (result.counts.inserted, result.counts.updated, result.counts.unchanged) == (2, 0, 0)
    assert rates.count() == 2
    assert rates.latest_date("EUR") == D
    assert rates.latest_date("USD") is None


def test_same_data_again_inserts_and_updates_nothing(repos):
    rates, _ = repos
    batch = [rate("USD", "1.0989"), rate("GBP", "0.83641")]
    rates.upsert_many(batch)
    result = rates.upsert_many(batch)
    assert (result.counts.inserted, result.counts.updated, result.counts.unchanged) == (0, 0, 2)
    assert rates.count() == 2  # no duplicates


def test_numerically_equal_rate_is_not_an_update(repos):
    rates, _ = repos
    rates.upsert_many([rate("USD", "1.0989")])
    result = rates.upsert_many([rate("USD", "1.09890")])  # same number, extra zero
    assert result.counts.updated == 0 and result.counts.unchanged == 1


def test_changed_rate_is_one_update(repos, pool):
    rates, _ = repos
    rates.upsert_many([rate("USD", "1.0989"), rate("GBP", "0.83641")])
    with pool.connection() as conn:
        first_seen, updated_before = conn.execute(
            "SELECT first_seen_at, updated_at FROM exchange_rates WHERE quote = 'USD'"
        ).fetchone()

    result = rates.upsert_many([rate("USD", "1.1000"), rate("GBP", "0.83641")])

    assert (result.counts.inserted, result.counts.updated, result.counts.unchanged) == (0, 1, 1)
    with pool.connection() as conn:
        value, first_seen_after, updated_after = conn.execute(
            "SELECT rate, first_seen_at, updated_at FROM exchange_rates WHERE quote = 'USD'"
        ).fetchone()
    assert value == Decimal("1.1000")
    assert first_seen_after == first_seen  # remembers when we first saw it
    assert updated_after > updated_before  # but notes the change


def test_mixed_batch_counts(repos):
    rates, _ = repos
    rates.upsert_many([rate("USD", "1.0")])
    result = rates.upsert_many([rate("USD", "2.0"), rate("GBP", "0.8")])
    assert (result.counts.inserted, result.counts.updated) == (1, 1)


def test_unknown_currency_is_skipped_not_fatal(repos, caplog):
    rates, _ = repos
    good = rate("USD", "1.0989")
    bad = rate("ZZZ", "5")  # ZZZ is not in the currencies table
    with caplog.at_level(logging.WARNING):
        result = rates.upsert_many([good, bad])
    assert result.counts.inserted == 1
    assert result.unknown_currency == [bad]
    assert rates.count() == 1  # the good one was still saved
    assert "ZZZ" in caplog.text


def test_empty_batch(repos):
    rates, _ = repos
    result = rates.upsert_many([])
    assert result.counts.inserted == 0 and rates.count() == 0


def test_database_rejects_bad_data_directly(repos, pool):
    """The CHECK constraints are a second line of defence behind the parser."""
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation), pool.connection() as conn:
        conn.execute("INSERT INTO exchange_rates (rate_date, base, quote, rate) "
                     "VALUES ('2026-11-20', 'EUR', 'USD', -1)")
    with pytest.raises(psycopg.errors.ForeignKeyViolation), pool.connection() as conn:
        conn.execute("INSERT INTO exchange_rates (rate_date, base, quote, rate) "
                     "VALUES ('2026-11-20', 'EUR', 'ZZZ', 1)")


def test_import_run_lifecycle(pool):
    runs = ImportRunRepository(pool)
    run_id = runs.start({"base": "EUR", "date": D})
    row = runs.get(run_id)
    assert row["status"] == "running" and row["finished_at"] is None
    assert row["params"] == {"base": "EUR", "date": "2026-11-20"}

    runs.finish(run_id, "partial", fetched=10, inserted=7, updated=1, rejected=2)
    row = runs.get(run_id)
    assert row["status"] == "partial" and row["finished_at"] is not None
    assert (row["fetched"], row["inserted"], row["updated"], row["rejected"]) == (10, 7, 1, 2)

    failed = runs.start({})
    runs.finish(failed, "failed", error_message="boom")
    assert runs.get(failed)["error_message"] == "boom"
