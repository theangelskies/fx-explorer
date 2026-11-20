"""Pydantic models of the RAW shapes the Frankfurter API returns.

"Raw" means: exactly what the API sends, before we clean or trust it. The shapes
come from the real responses saved in docs/samples/. Dates stay as text here; the
parser turns them into real dates and decides what to do with bad ones.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class RawRate(BaseModel):
    """One item of GET /rates, e.g. {"date": "2026-11-20", "base": "EUR", "quote": "USD", "rate": 1.0989}."""

    model_config = ConfigDict(extra="ignore")  # e.g. the optional `providers` list is not needed

    date: str
    base: str
    quote: str
    rate: Decimal


class RawCurrency(BaseModel):
    """One item of GET /currencies."""

    model_config = ConfigDict(extra="ignore")

    iso_code: str
    name: str
    type: str
    iso_numeric: str | None = None
    symbol: str | None = None
    start_date: str | None = None
    end_date: str | None = None
