"""GET /v1/rates (listRates)."""

import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query

from fx_explorer.api.deps import currency_repository, rate_repository
from fx_explorer.api.models import Page, PageInfo, RateOut
from fx_explorer.api.pagination import decode_cursor, encode_cursor, invalid_cursor
from fx_explorer.api.params import (
    CODE_PATTERN,
    DATE_PATTERN,
    ParamErrors,
    check_known,
    parse_codes,
    parse_date,
)
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.rates import RateRepository

router = APIRouter(tags=["Rates"])


def _decode_rates_cursor(cursor: str) -> tuple[date, str]:
    values = decode_cursor(cursor, {"d", "q"})
    if not re.fullmatch(CODE_PATTERN, values["q"]):
        raise invalid_cursor()
    try:
        return date.fromisoformat(values["d"]), values["q"]
    except ValueError:
        raise invalid_cursor() from None


@router.get("/v1/rates", response_model=Page[RateOut])
def list_rates(
    base: str = Query("EUR", pattern=CODE_PATTERN),
    quotes: str | None = Query(None),
    from_: str | None = Query(None, alias="from", pattern=DATE_PATTERN),
    to: str | None = Query(None, pattern=DATE_PATTERN),
    sort: Literal["date_desc", "date_asc"] = Query("date_desc"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, min_length=1, max_length=512),
    currencies: CurrencyRepository = Depends(currency_repository),
    rates: RateRepository = Depends(rate_repository),
) -> Page[RateOut]:
    # 1. Check the shape of every parameter (no database needed yet).
    errors = ParamErrors()
    quote_list = parse_codes(quotes, "quotes", errors)
    date_from = parse_date(from_, "from", errors)
    date_to = parse_date(to, "to", errors)
    if date_from and date_to and date_from > date_to:
        errors.add("from", f"Must be on or before `to` ({date_to}).")
    errors.raise_if_any()
    after = _decode_rates_cursor(cursor) if cursor is not None else None

    # 2. The currencies must exist. (A valid-looking but unknown code is a client mistake,
    #    400, while "known currency, no rates in range" is a normal empty result, 200.)
    known = currencies.find_known([base, *(quote_list or [])])
    check_known("base", [base], known, errors)
    check_known("quotes", quote_list or [], known, errors)
    errors.raise_if_any()

    # 3. Read one page.
    page = rates.list_page(
        base=base,
        limit=limit,
        quotes=quote_list,
        date_from=date_from,
        date_to=date_to,
        newest_first=sort == "date_desc",
        after=after,
    )
    next_cursor = None
    if page.has_more:
        last = page.items[-1]
        next_cursor = encode_cursor({"d": last.rate_date.isoformat(), "q": last.quote})
    return Page[RateOut](
        data=[RateOut(**vars(r)) for r in page.items],
        page=PageInfo(limit=limit, next_cursor=next_cursor),
    )
