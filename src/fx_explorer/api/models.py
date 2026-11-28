"""Pydantic request/response models. Each one mirrors a schema in openapi.yaml.

These are NOT the source of truth: openapi.yaml is. If they disagree, fix this file.
Response models make FastAPI check our own output before sending it, so a bug that would
break the contract becomes a 500 in tests instead of a silent surprise for clients.

Two serialisation rules from the contract live here:
- Decimals go out as plain decimal STRINGS (ADR 0004): format(d, "f") never uses exponent
  notation, so Decimal("1.7454E-5") becomes "0.000017454".
- Timestamps go out in UTC with a "Z" (docs/api-design.md, section 9).
"""

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator


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


# --------------------------------------------------------------------- alerts

# Threshold: digits, optionally a dot and 1-10 decimals (contract: Threshold). "Greater than 0"
# is checked on the Decimal value (the contract's regex does it with a lookahead, which the
# regex engine pydantic uses doesn't support).
THRESHOLD_PATTERN = r"^[0-9]+(\.[0-9]{1,10})?$"
THRESHOLD_MESSAGE = 'Must be a number greater than 0 with at most 10 decimal places, e.g. "1.2500".'
AlertCondition = Literal["above", "below"]


class AlertCreateIn(BaseModel):
    """#/components/schemas/AlertCreate: checks the SHAPE of the body.

    Rules that need the database (currencies exist, the pair has rates) and the cross-field
    rule (quote != base) are checked in the route, so all problems are reported together.
    """

    model_config = ConfigDict(extra="forbid")  # unknown fields -> 422 "Unknown field."

    base: str
    quote: str
    condition: AlertCondition
    # Any, so a JSON number (1.2) gets our helpful message instead of "Must be a string".
    threshold: Any
    note: str | None = Field(default=None, max_length=200)

    @field_validator("base", "quote")
    @classmethod
    def _code_format(cls, value: str) -> str:
        from fx_explorer.api.params import CODE_MESSAGE, CODE_PATTERN  # avoid import cycle

        if not re.fullmatch(CODE_PATTERN, value):
            raise ValueError(CODE_MESSAGE)
        return value

    @field_validator("threshold")
    @classmethod
    def _threshold(cls, value: Any) -> Decimal:
        if not isinstance(value, str) or len(value) > 40 or not re.fullmatch(THRESHOLD_PATTERN, value):
            raise ValueError(THRESHOLD_MESSAGE)
        number = Decimal(value)
        if number <= 0:
            raise ValueError(THRESHOLD_MESSAGE)
        return number


class AlertOut(BaseModel):
    """#/components/schemas/Alert"""

    id: UUID
    base: str
    quote: str
    condition: AlertCondition
    threshold: DecimalStr
    note: str | None
    created_at: UtcDateTime
    current_rate: DecimalStr | None
    triggered: bool


# --------------------------------------------------------------------- movers

class MoverOut(BaseModel):
    """#/components/schemas/Mover"""

    quote: str
    rate_date: date
    rate: DecimalStr
    previous_rate_date: date
    previous_rate: DecimalStr
    change_abs: DecimalStr
    change_pct: DecimalStr


class MoverListOut(BaseModel):
    """#/components/schemas/MoverList"""

    base: str
    data: list[MoverOut]


# ---------------------------------------------------------------- import runs

class ImportRunOut(BaseModel):
    """#/components/schemas/ImportRun"""

    id: int
    status: Literal["running", "success", "partial", "failed"]
    started_at: UtcDateTime
    finished_at: UtcDateTime | None
    fetched: int
    inserted: int
    updated: int
    rejected: int
    error_message: str | None


# --------------------------------------------------------------------- agent (ADR 0009)

class AgentAskIn(BaseModel):
    """#/components/schemas/AgentAskRequest"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    conversation_id: UUID | None = None


class AgentSourceOut(BaseModel):
    """#/components/schemas/AgentSource"""

    label: str
    request: str


class AgentToolCallOut(BaseModel):
    """#/components/schemas/AgentToolCall"""

    tool: str
    input: dict[str, Any]
    request: str | None
    status: int | str | None
    result_summary: str
    is_error: bool
    duration_ms: int


class AgentStepOut(BaseModel):
    """#/components/schemas/AgentStep"""

    step: int
    model_text: str
    tool_calls: list[AgentToolCallOut]
    input_tokens: int
    output_tokens: int
    duration_ms: int


class AgentAlertOut(BaseModel):
    """The proposed alert: #/components/schemas/AlertCreate (threshold stays the text the agent gave)."""

    base: str
    quote: str
    condition: AlertCondition
    threshold: str
    note: str | None


class AgentPendingActionOut(BaseModel):
    """#/components/schemas/AgentPendingAction"""

    id: UUID
    tool: Literal["create_alert"]
    status: Literal["pending", "confirmed", "cancelled"]
    alert: AgentAlertOut


class AgentUsageOut(BaseModel):
    """#/components/schemas/AgentUsage"""

    input_tokens: int
    output_tokens: int
    cost_usd: str | None
    duration_ms: int


class AgentAnswerOut(BaseModel):
    """#/components/schemas/AgentAnswer"""

    conversation_id: UUID
    answer: str
    status: Literal["answered", "max_steps", "refused"]
    sources: list[AgentSourceOut]
    steps: list[AgentStepOut]
    pending_action: AgentPendingActionOut | None
    usage: AgentUsageOut
