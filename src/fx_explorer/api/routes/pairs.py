"""GET /v1/pairs/{base}/{quote} (getPair)."""

from decimal import Decimal

from fastapi import APIRouter, Depends, Path, Query

from fx_explorer.api.deps import rate_repository
from fx_explorer.api.errors import ProblemError
from fx_explorer.api.models import (
    PairDetailOut,
    PairStatsOut,
    RatePointOut,
    SeriesPointOut,
    Window,
)
from fx_explorer.api.params import CODE_PATTERN
from fx_explorer.repositories.rates import RateRepository

router = APIRouter(tags=["Pairs"])

WINDOW_DAYS: dict[str, int] = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}

# Rounding rules from the contract (PairStats.avg, PairDetail.change_pct)
AVG_PLACES = Decimal("1E-10")  # 10 decimal places
PCT_PLACES = Decimal("1E-4")  # 4 decimal places


def _round_avg(value: Decimal) -> Decimal:
    """Round to 10 places, then drop trailing zeros ("1.1051000000" -> "1.1051")."""
    rounded = value.quantize(AVG_PLACES)
    return rounded.normalize() if rounded != 0 else rounded


@router.get("/v1/pairs/{base}/{quote}", response_model=PairDetailOut)
def get_pair(
    base: str = Path(pattern=CODE_PATTERN),
    quote: str = Path(pattern=CODE_PATTERN),
    window: Window = Query("30d"),
    rates: RateRepository = Depends(rate_repository),
) -> PairDetailOut:
    w = rates.pair_window(base, quote, WINDOW_DAYS[window])
    if w is None:
        # Covers unknown currencies too: either way there is nothing to show (contract: 404).
        raise ProblemError("not-found", f"No rates stored for {base}/{quote}.")

    change_abs = change_pct = None
    if w.previous is not None:
        change_abs = w.latest.rate - w.previous.rate  # Decimal subtraction: exact
        change_pct = (change_abs / w.previous.rate * 100).quantize(PCT_PLACES)

    stats = None
    if w.stats is not None:
        stats = PairStatsOut(min=w.stats.min, max=w.stats.max, avg=_round_avg(w.stats.avg))

    return PairDetailOut(
        base=base,
        quote=quote,
        window=window,
        latest=RatePointOut(rate_date=w.latest.rate_date, rate=w.latest.rate),
        previous=(RatePointOut(rate_date=w.previous.rate_date, rate=w.previous.rate)
                  if w.previous else None),
        change_abs=change_abs,
        change_pct=change_pct,
        stats=stats,
        series=[SeriesPointOut(date=p.rate_date, rate=p.rate) for p in w.series],
    )
