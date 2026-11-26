"""GET /v1/currencies (listCurrencies) and GET /v1/currencies/{code} (getCurrency)."""

import re
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query

from fx_explorer.api.deps import currency_repository
from fx_explorer.api.errors import ProblemError
from fx_explorer.api.models import CurrencyOut, CurrencyType, Page, PageInfo
from fx_explorer.api.pagination import decode_cursor, encode_cursor, invalid_cursor
from fx_explorer.api.params import CODE_PATTERN
from fx_explorer.repositories.currencies import CurrencyRepository, CurrencySummary

router = APIRouter(tags=["Currencies"])


def _out(c: CurrencySummary) -> CurrencyOut:
    return CurrencyOut(**vars(c))


@router.get("/v1/currencies", response_model=Page[CurrencyOut])
def list_currencies(
    type_: CurrencyType | None = Query(None, alias="type"),
    q: str | None = Query(None, min_length=1, max_length=100),
    # A plain bool would also accept yes/no/1/0/on/off. The contract says only true/false.
    has_rates: Literal["true", "false"] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, min_length=1, max_length=512),
    repo: CurrencyRepository = Depends(currency_repository),
) -> Page[CurrencyOut]:
    after_code = None
    if cursor is not None:
        after_code = decode_cursor(cursor, {"c"})["c"]
        if not re.fullmatch(CODE_PATTERN, after_code):
            raise invalid_cursor()

    page = repo.list_page(
        limit=limit,
        type_=type_,
        q=q,
        has_rates=None if has_rates is None else has_rates == "true",
        after_code=after_code,
    )
    next_cursor = encode_cursor({"c": page.items[-1].iso_code}) if page.has_more else None
    return Page[CurrencyOut](
        data=[_out(c) for c in page.items],
        page=PageInfo(limit=limit, next_cursor=next_cursor),
    )


@router.get("/v1/currencies/{code}", response_model=CurrencyOut)
def get_currency(
    code: str = Path(pattern=CODE_PATTERN),
    repo: CurrencyRepository = Depends(currency_repository),
) -> CurrencyOut:
    currency = repo.get(code)
    if currency is None:
        raise ProblemError("not-found", f"No currency with code '{code}'.")
    return _out(currency)
