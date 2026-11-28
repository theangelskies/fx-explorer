"""Trend statistics for a pair (docs/decisions/0005-long-range-trends.md).

Pure functions: they take (date, Decimal) points and return numbers. No database, no HTTP,
so they're easy to test and can be reused by anything (the API now, an agent later).

All maths is Decimal, including ln, square roots and fractional powers, so results are
exact to 28 significant digits before rounding. The rounding rules are the contract's
(openapi.yaml, PairTrend) and live here, so every caller rounds the same way:
  - percentages: 4 decimal places (half to even)
  - rate-valued numbers (averages): 10 decimal places, trailing zeros removed
  - change_abs: exact (a subtraction of two stored rates needs no rounding)
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

Point = tuple[dt.date, Decimal]

PCT_PLACES = Decimal("1E-4")  # 4 decimal places
RATE_PLACES = Decimal("1E-10")  # 10 decimal places
DAYS_PER_YEAR = Decimal("365.25")  # average year length, incl. leap years
RETURNS_PER_YEAR = 365  # one rate per calendar day (ADR 0005: why not 252)
MIN_DAYS_FOR_ANNUALIZED = 365
MIN_RETURNS_FOR_VOLATILITY = 20
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Move:
    date: dt.date  # the day the move ended
    change_pct: Decimal


@dataclass(frozen=True)
class Stats:
    min: Decimal
    max: Decimal
    avg: Decimal


@dataclass(frozen=True)
class Trend:
    period_start: dt.date
    period_end: dt.date
    change_abs: Decimal
    change_pct: Decimal
    annualized_change_pct: Decimal | None
    volatility_annualized_pct: Decimal | None
    biggest_rise: Move | None
    biggest_fall: Move | None
    moving_average_30: Decimal | None
    moving_average_200: Decimal | None


# ------------------------------------------------------------------ rounding

def round_pct(value: Decimal) -> Decimal:
    return value.quantize(PCT_PLACES)


def round_rate(value: Decimal) -> Decimal:
    """10 decimal places, then drop trailing zeros: 1.1051000000 -> 1.1051."""
    rounded = value.quantize(RATE_PLACES)
    return rounded.normalize() if rounded != 0 else rounded


def pct_change(old: Decimal, new: Decimal) -> Decimal:
    """(new / old − 1) × 100, unrounded."""
    return (new / old - 1) * HUNDRED


# ---------------------------------------------------------------- functions

def day_change(previous: Decimal | None, latest: Decimal) -> tuple[Decimal | None, Decimal | None]:
    """Top-level change_abs / change_pct of PairDetail: latest vs the previous stored rate."""
    if previous is None:
        return None, None
    return latest - previous, round_pct(pct_change(previous, latest))


def window_stats(points: Sequence[Point]) -> Stats | None:
    if not points:
        return None
    rates = [rate for _, rate in points]
    return Stats(min(rates), max(rates), round_rate(sum(rates) / len(rates)))


def moving_average(recent: Sequence[Point], n: int) -> Decimal | None:
    """Mean of the last n rates (recent must be oldest-first). None if there are fewer than n."""
    if len(recent) < n:
        return None
    return round_rate(sum(rate for _, rate in recent[-n:]) / n)


def daily_moves(points: Sequence[Point]) -> list[Move]:
    """The % move between each pair of consecutive stored rates (unrounded)."""
    return [Move(d2, pct_change(r1, r2)) for (_, r1), (d2, r2) in zip(points, points[1:])]


def volatility_annualized_pct(points: Sequence[Point]) -> Decimal | None:
    """stdev(daily log returns) × √365 × 100, sample stdev (n − 1). None if < 20 returns."""
    log_returns = [(r2 / r1).ln() for (_, r1), (_, r2) in zip(points, points[1:])]
    n = len(log_returns)
    if n < MIN_RETURNS_FOR_VOLATILITY:
        return None
    mean = sum(log_returns) / n
    variance = sum((x - mean) ** 2 for x in log_returns) / (n - 1)
    return round_pct(variance.sqrt() * Decimal(RETURNS_PER_YEAR).sqrt() * HUNDRED)


def annualized_change_pct(first: Point, last: Point) -> Decimal | None:
    """((last / first) ^ (365.25 / days) − 1) × 100. None if the period is under 365 days."""
    days = (last[0] - first[0]).days
    if days < MIN_DAYS_FOR_ANNUALIZED:
        return None
    growth = last[1] / first[1]
    return round_pct((growth ** (DAYS_PER_YEAR / days) - 1) * HUNDRED)


def compute_trend(points: Sequence[Point], recent: Sequence[Point] | None = None) -> Trend | None:
    """The PairTrend for a window.

    points: every stored daily rate in the window, oldest first.
    recent: the last (up to) 200 stored rates up to the window's end, oldest first, for the
            moving averages (they may reach back before the window). Defaults to `points`.
    Returns None for an empty window.
    """
    if not points:
        return None
    with localcontext() as ctx:
        ctx.prec = 28  # explicit, so a caller's context can't change our results
        first, last = points[0], points[-1]
        moves = daily_moves(points)
        rises = [m for m in moves if m.change_pct > 0]
        falls = [m for m in moves if m.change_pct < 0]
        # max()/min() return the FIRST of equal values, i.e. the earliest date wins a tie.
        rise = max(rises, key=lambda m: m.change_pct, default=None)
        fall = min(falls, key=lambda m: m.change_pct, default=None)
        recent = points if recent is None else recent
        return Trend(
            period_start=first[0],
            period_end=last[0],
            change_abs=last[1] - first[1],
            change_pct=round_pct(pct_change(first[1], last[1])),
            annualized_change_pct=annualized_change_pct(first, last),
            volatility_annualized_pct=volatility_annualized_pct(points),
            biggest_rise=Move(rise.date, round_pct(rise.change_pct)) if rise else None,
            biggest_fall=Move(fall.date, round_pct(fall.change_pct)) if fall else None,
            moving_average_30=moving_average(recent, 30),
            moving_average_200=moving_average(recent, 200),
        )
