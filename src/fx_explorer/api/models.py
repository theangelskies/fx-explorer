"""Pydantic request/response models. Each one mirrors a schema in openapi.yaml.

These are NOT the source of truth: openapi.yaml is. If they disagree, fix this file.
Response models make FastAPI check our own output before sending it, so a bug that would
break the contract becomes a 500 in tests instead of a silent surprise for clients.

Two serialisation rules from the contract live here:
- Decimals go out as plain decimal STRINGS (ADR 0004): format(d, "f") never uses exponent
  notation, so Decimal("1.7454E-5") becomes "0.000017454".
- Timestamps go out in UTC with a "Z" (docs/api-design.md, section 9).
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, PlainSerializer


def _decimal_str(value: Decimal) -> str:
    text = format(value, "f")
    return text[1:] if text.startswith("-") and value == 0 else text  # never "-0.00"


def _utc_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


DecimalStr = Annotated[Decimal, PlainSerializer(_decimal_str, return_type=str)]
UtcDateTime = Annotated[datetime, PlainSerializer(_utc_z, return_type=str)]
CurrencyType = Literal["currency", "metal", "crypto"]
Window = Literal["7d", "30d", "90d", "1y", "3y", "5y", "max"]
SeriesInterval = Literal["day", "week", "month"]

T = TypeVar("T")


# --------------------------------------------------------------------- shared

class PageInfo(BaseModel):
    """#/components/schemas/PageInfo"""

    limit: int
    next_cursor: str | None


class Page(BaseModel, Generic[T]):
    """The list envelope: {"data": [...], "page": {...}}"""

    data: list[T]
    page: PageInfo


class Health(BaseModel):
    """#/components/schemas/Health"""

    status: Literal["ok"]
    database: Literal["ok"]


# ----------------------------------------------------------------- currencies

class CurrencyOut(BaseModel):
    """#/components/schemas/Currency"""

    iso_code: str
    name: str
    type: CurrencyType
    iso_numeric: str | None
    symbol: str | None
    start_date: date | None
    end_date: date | None
    rates_count: int
    latest_rate_date: date | None


# ---------------------------------------------------------------------- rates

class RateOut(BaseModel):
    """#/components/schemas/Rate"""

    rate_date: date
    base: str
    quote: str
    rate: DecimalStr
    updated_at: UtcDateTime


# ---------------------------------------------------------------------- pairs

class RatePointOut(BaseModel):
    """#/components/schemas/RatePoint"""

    rate_date: date
    rate: DecimalStr


class SeriesPointOut(BaseModel):
    """#/components/schemas/SeriesPoint"""

    date: date
    rate: DecimalStr


class PairStatsOut(BaseModel):
    """#/components/schemas/PairStats"""

    min: DecimalStr
    max: DecimalStr
    avg: DecimalStr


class TrendMoveOut(BaseModel):
    """#/components/schemas/TrendMove"""

    date: date
    change_pct: DecimalStr


class PairTrendOut(BaseModel):
    """#/components/schemas/PairTrend"""

    period_start: date
    period_end: date
    change_abs: DecimalStr
    change_pct: DecimalStr
    annualized_change_pct: DecimalStr | None
    volatility_annualized_pct: DecimalStr | None
    biggest_rise: TrendMoveOut | None
    biggest_fall: TrendMoveOut | None
    moving_average_30: DecimalStr | None
    moving_average_200: DecimalStr | None


class PairDetailOut(BaseModel):
    """#/components/schemas/PairDetail"""

    base: str
    quote: str
    window: Window
    window_start: date
    series_interval: SeriesInterval
    first_available_date: date
    data_points: int
    latest: RatePointOut
    previous: RatePointOut | None
    change_abs: DecimalStr | None
    change_pct: DecimalStr | None
    stats: PairStatsOut | None
    trend: PairTrendOut | None
    series: list[SeriesPointOut]
