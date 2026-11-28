"""GET /v1/pairs/{base}/{quote} (getPair).

The route only connects the pieces: the repository reads the history, analytics/trends.py
computes the numbers, and the models shape the response exactly as the contract says.
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends, Path, Query

from fx_explorer.analytics import trends
from fx_explorer.api.deps import rate_repository
from fx_explorer.api.errors import ProblemError
from fx_explorer.api.models import (
    PairDetailOut,
    PairStatsOut,
    PairTrendOut,
    RatePointOut,
    SeriesInterval,
    SeriesPointOut,
    TrendMoveOut,
    Window,
)
from fx_explorer.api.params import CODE_PATTERN
from fx_explorer.repositories.rates import RatePoint, RateRepository

router = APIRouter(tags=["Pairs"])

# Window lengths in calendar days, ending at (and counting) the latest stored date.
# None = all stored history. Same numbers as the Window schema in openapi.yaml.
WINDOW_DAYS: dict[str, int | None] = {
    "7d": 7, "30d": 30, "90d": 90, "1y": 365, "3y": 1095, "5y": 1825, "max": None,
}
# Chart spacing when `interval` is omitted (contract: Interval parameter).
DEFAULT_INTERVAL: dict[str, str] = {
    "7d": "day", "30d": "day", "90d": "day", "1y": "day",
    "3y": "week", "5y": "week", "max": "month",
}


def _points(rows: list[RatePoint]) -> list[trends.Point]:
    return [(r.rate_date, r.rate) for r in rows]


def _trend_out(t: trends.Trend | None) -> PairTrendOut | None:
    if t is None:
        return None
    move = lambda m: TrendMoveOut(date=m.date, change_pct=m.change_pct) if m else None  # noqa: E731
    return PairTrendOut(**{**asdict(t), "biggest_rise": move(t.biggest_rise),
                           "biggest_fall": move(t.biggest_fall)})


@router.get("/v1/pairs/{base}/{quote}", response_model=PairDetailOut)
def get_pair(
    base: str = Path(pattern=CODE_PATTERN),
    quote: str = Path(pattern=CODE_PATTERN),
    window: Window = Query("30d"),
    interval: SeriesInterval | None = Query(None),
    rates: RateRepository = Depends(rate_repository),
) -> PairDetailOut:
    series_interval = interval or DEFAULT_INTERVAL[window]
    h = rates.pair_history(base, quote, WINDOW_DAYS[window], series_interval)
    if h is None:
        # Covers unknown currencies too: either way there is nothing to show (contract: 404).
        raise ProblemError("not-found", f"No rates stored for {base}/{quote}.")

    points = _points(h.points)
    change_abs, change_pct = trends.day_change(
        h.previous.rate if h.previous else None, h.latest.rate)
    stats = trends.window_stats(points)

    return PairDetailOut(
        base=base,
        quote=quote,
        window=window,
        window_start=h.window_start,
        series_interval=series_interval,
        first_available_date=h.first_available_date,
        data_points=len(points),
        latest=RatePointOut(rate_date=h.latest.rate_date, rate=h.latest.rate),
        previous=(RatePointOut(rate_date=h.previous.rate_date, rate=h.previous.rate)
                  if h.previous else None),
        change_abs=change_abs,
        change_pct=change_pct,
        stats=PairStatsOut(min=stats.min, max=stats.max, avg=stats.avg) if stats else None,
        trend=_trend_out(trends.compute_trend(points, _points(h.recent))),
        series=[SeriesPointOut(date=p.rate_date, rate=p.rate) for p in h.series],
    )
