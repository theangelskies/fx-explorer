"""GET /v1/movers (listMovers): the biggest day-over-day moves (ADR 0007)."""

from typing import Literal

from fastapi import APIRouter, Depends, Query

from fx_explorer.analytics.trends import pct_change, round_pct
from fx_explorer.api.deps import currency_repository, rate_repository
from fx_explorer.api.models import MoverListOut, MoverOut
from fx_explorer.api.params import CODE_PATTERN, ParamErrors, check_known
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.rates import RateRepository

router = APIRouter(tags=["Rates"])


@router.get("/v1/movers", response_model=MoverListOut)
def list_movers(
    base: str = Query("EUR", pattern=CODE_PATTERN),
    limit: int = Query(5, ge=1, le=50),
    direction: Literal["up", "down"] | None = Query(None),
    currencies: CurrencyRepository = Depends(currency_repository),
    rates: RateRepository = Depends(rate_repository),
) -> MoverListOut:
    errors = ParamErrors()
    check_known("base", [base], currencies.find_known([base]), errors)
    errors.raise_if_any()

    moves = []
    for m in rates.latest_moves(base):
        change = pct_change(m.previous_rate, m.rate)  # unrounded, for exact sorting
        if (direction == "up" and change <= 0) or (direction == "down" and change >= 0):
            continue
        moves.append((change, m))
    # Biggest move first (by size, up or down), ties by quote A->Z.
    moves.sort(key=lambda item: (-abs(item[0]), item[1].quote))

    return MoverListOut(
        base=base,
        data=[
            MoverOut(
                quote=m.quote,
                rate_date=m.rate_date,
                rate=m.rate,
                previous_rate_date=m.previous_rate_date,
                previous_rate=m.previous_rate,
                change_abs=m.rate - m.previous_rate,
                change_pct=round_pct(change),  # same rounding as getPair (analytics/trends.py)
            )
            for change, m in moves[:limit]
        ],
    )
