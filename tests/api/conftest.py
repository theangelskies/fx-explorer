"""Shared fixtures for API tests: the real app on the test database, called in-process."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from fx_explorer.api.app import create_app

CONTRACT = Path(__file__).resolve().parents[2] / "openapi.yaml"


@pytest.fixture(scope="session")
def contract() -> dict:
    """openapi.yaml as a Python dict, for tests that compare code against the contract."""
    return yaml.safe_load(CONTRACT.read_text())


@pytest.fixture
def app(pool):
    return create_app(pool=pool)


@pytest.fixture
def client(app):
    # TestClient calls the app directly in memory: no server or network needed.
    with TestClient(app) as c:
        yield c


# Real EUR -> USD values from docs/samples/c_weekend_range.json
EUR_USD = {
    "2026-11-13": "1.1076",
    "2026-11-14": "1.1072",
    "2026-11-15": "1.1068",
    "2026-11-16": "1.1034",
    "2026-11-17": "1.1005",
}


def seed(pool) -> None:
    """A small realistic dataset: 6 currencies, 11 EUR-based rates (no BTC/USDT rates)."""
    from datetime import date
    from decimal import Decimal

    from fx_explorer.domain.models import Currency, ExchangeRate
    from fx_explorer.repositories.currencies import CurrencyRepository
    from fx_explorer.repositories.rates import RateRepository

    CurrencyRepository(pool).upsert_many([
        Currency(iso_code="EUR", name="Euro", type="currency", iso_numeric="978", symbol="€",
                 start_date=date(1999, 1, 1), end_date=date(2026, 11, 20)),
        Currency(iso_code="USD", name="United States Dollar", type="currency", iso_numeric="840",
                 symbol="$", start_date=date(1948, 6, 21), end_date=date(2026, 11, 20)),
        Currency(iso_code="GBP", name="British Pound", type="currency", iso_numeric="826", symbol="£"),
        Currency(iso_code="XAU", name="Gold (Troy Ounce)", type="metal", iso_numeric="959", symbol="oz t"),
        Currency(iso_code="BTC", name="Bitcoin", type="crypto", symbol="₿"),
        Currency(iso_code="USDT", name="Tether USD", type="crypto"),
    ])
    rates = [ExchangeRate(rate_date=date.fromisoformat(d), base="EUR", quote="USD", rate=Decimal(v))
             for d, v in EUR_USD.items()]
    rates += [ExchangeRate(rate_date=date.fromisoformat(d), base="EUR", quote="GBP",
                           rate=Decimal("0.83641")) for d in EUR_USD]
    rates.append(ExchangeRate(rate_date=date(2026, 11, 17), base="EUR", quote="XAU",
                              rate=Decimal("0.00029946")))
    RateRepository(pool).upsert_many(rates)


@pytest.fixture
def seeded(pool, client):
    seed(pool)
    return client
