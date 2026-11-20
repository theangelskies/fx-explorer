"""Our own clean models. Anything that reaches the database has been through these."""

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _clean_code(value: str) -> str:
    code = value.strip().upper()
    if not code:
        raise ValueError("currency code is empty")
    return code


class Currency(BaseModel):
    model_config = ConfigDict(frozen=True)

    iso_code: str
    iso_numeric: str | None = None
    name: str
    symbol: str | None = None
    type: Literal["currency", "metal", "crypto"]
    start_date: dt.date | None = None
    end_date: dt.date | None = None

    _normalise_code = field_validator("iso_code")(_clean_code)


class ExchangeRate(BaseModel):
    model_config = ConfigDict(frozen=True)

    rate_date: dt.date
    base: str
    quote: str
    rate: Decimal = Field(gt=0, allow_inf_nan=False)

    _normalise_base = field_validator("base")(_clean_code)
    _normalise_quote = field_validator("quote")(_clean_code)

    @model_validator(mode="after")
    def _no_self_pair(self) -> "ExchangeRate":
        if self.base == self.quote:
            raise ValueError("base and quote must differ")
        return self
