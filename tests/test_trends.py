"""analytics/trends.py on small hand-made series. Every expected number is worked out by hand
in the comments, so you can check the formulas without trusting the code."""

import datetime as dt
from decimal import Decimal

import pytest

from fx_explorer.analytics.trends import (
    Move,
    Stats,
    compute_trend,
    day_change,
    window_stats,
)

D = Decimal


def series(start: dt.date, *rates: str):
    """Consecutive calendar days from `start` with the given rates."""
    return [(start + dt.timedelta(days=i), D(r)) for i, r in enumerate(rates)]


# --------------------------------------------------------------------------------------
# Series A: five days, moves of +2%, −2%, +5%, 0%
#   01-01  1.00
#   01-02  1.02      1.00 × 1.02          → +2%
#   01-03  0.9996    1.02 × 0.98          → −2%
#   01-04  1.04958   0.9996 × 1.05        → +5%
#   01-05  1.04958   unchanged            →  0%
A = series(dt.date(2024, 1, 1), "1.00", "1.02", "0.9996", "1.04958", "1.04958")


def test_series_a_change_and_moves():
    t = compute_trend(A)
    assert (t.period_start, t.period_end) == (dt.date(2024, 1, 1), dt.date(2024, 1, 5))
    # change_abs = last − first = 1.04958 − 1.00 = 0.04958 (exact, not rounded)
    assert t.change_abs == D("0.04958")
    # change_pct = (1.04958 / 1.00 − 1) × 100 = 4.958 → 4 decimal places: 4.9580
    assert str(t.change_pct) == "4.9580"
    # biggest rise is the +5% on 01-04 (bigger than the +2% on 01-02)
    assert t.biggest_rise == Move(dt.date(2024, 1, 4), D("5.0000"))
    # biggest (only) fall is the −2% on 01-03
    assert t.biggest_fall == Move(dt.date(2024, 1, 3), D("-2.0000"))


def test_series_a_short_period_gives_nulls():
    t = compute_trend(A)
    assert t.annualized_change_pct is None  # 4 days < 365
    assert t.volatility_annualized_pct is None  # 4 daily returns < 20
    assert t.moving_average_30 is None and t.moving_average_200 is None  # 5 rates < 30


def test_series_a_stats():
    # avg = (1.00 + 1.02 + 0.9996 + 1.04958 + 1.04958) / 5 = 5.11876 / 5 = 1.023752
    assert window_stats(A) == Stats(D("0.9996"), D("1.04958"), D("1.023752"))


# --------------------------------------------------------------------------------------
# Series B: annualised change. 2020-01-01 → 2024-01-01 is 1461 days = 4 × 365.25 exactly.
#   growth = 1.4641 / 1 = 1.1⁴, exponent = 365.25 / 1461 = 0.25
#   (1.1⁴)^0.25 = 1.1  →  (1.1 − 1) × 100 = 10% a year (while the total change is 46.41%)
B = [(dt.date(2020, 1, 1), D("1")), (dt.date(2024, 1, 1), D("1.4641"))]


def test_annualized_change():
    t = compute_trend(B)
    assert str(t.change_pct) == "46.4100"
    assert str(t.annualized_change_pct) == "10.0000"


def test_annualized_needs_365_days():
    # 2020 is a leap year: 01-01 → 12-30 is 364 days, 01-01 → 12-31 is 365 days
    short = [(dt.date(2020, 1, 1), D("1")), (dt.date(2020, 12, 30), D("1.1"))]
    year = [(dt.date(2020, 1, 1), D("1")), (dt.date(2020, 12, 31), D("1.1"))]
    assert compute_trend(short).annualized_change_pct is None
    # 365 days: (1.1)^(365.25/365) − 1 = slightly more than 10%
    assert compute_trend(year).annualized_change_pct > D("10")


# --------------------------------------------------------------------------------------
# Series C: volatility. 21 rates alternating 1, 1.1, 1, 1.1 … → 20 daily log returns:
#   +ln(1.1), −ln(1.1), … (10 of each). Let a = ln(1.1) = 0.0953101798.
#   mean = 0, sample variance = 20a² / (20 − 1), stdev = a × √(20/19) = 0.0977861812
#   annualised: 0.0977861812 × √365 (= 19.1049731745) × 100 = 186.82%
C = series(dt.date(2024, 1, 1), *(["1", "1.1"] * 10 + ["1"]))


def test_volatility_to_2_decimal_places():
    t = compute_trend(C)
    assert t.volatility_annualized_pct.quantize(D("0.01")) == D("186.82")


def test_volatility_needs_20_returns():
    assert compute_trend(C[:20]).volatility_annualized_pct is None  # 20 rates = 19 returns
    assert compute_trend(C[:21]).volatility_annualized_pct is not None  # 21 rates = 20 returns


def test_ties_pick_the_earliest_date():
    t = compute_trend(C)
    # every rise is +10% (1 → 1.1); the first one ends on 01-02
    assert t.biggest_rise == Move(dt.date(2024, 1, 2), D("10.0000"))
    # every fall is 1 / 1.1 − 1 = −9.0909…%; the first one ends on 01-03
    assert t.biggest_fall == Move(dt.date(2024, 1, 3), D("-9.0909"))
    assert t.change_abs == 0  # starts and ends at 1


# --------------------------------------------------------------------------------------
# Series D: moving averages. 250 days with rates 1, 2, 3, … 250.
#   MA30  = mean(221 … 250) = (221 + 250) / 2 = 235.5
#   MA200 = mean(51 … 250)  = (51 + 250) / 2  = 150.5
DSER = series(dt.date(2024, 1, 1), *[str(i) for i in range(1, 251)])


def test_moving_averages():
    t = compute_trend(DSER)
    assert (t.moving_average_30, t.moving_average_200) == (D("235.5"), D("150.5"))


def test_moving_average_200_needs_200_rates():
    t = compute_trend(DSER[:199])  # rates 1 … 199
    assert t.moving_average_200 is None
    assert t.moving_average_30 == D("184.5")  # mean(170 … 199)


def test_moving_averages_can_reach_before_the_window():
    """A 7-day window still gets MA200 from the last 200 stored rates (contract)."""
    t = compute_trend(DSER[-7:], recent=DSER[-200:])
    assert t.period_start == dt.date(2024, 1, 1) + dt.timedelta(days=243)
    assert t.moving_average_200 == D("150.5")


# ------------------------------------------------------------------------------ edges

def test_no_points():
    assert compute_trend([]) is None
    assert window_stats([]) is None


def test_one_point():
    t = compute_trend([(dt.date(2024, 1, 1), D("1.0948"))])
    assert (t.change_abs, str(t.change_pct)) == (0, "0.0000")
    assert t.biggest_rise is None and t.biggest_fall is None
    assert t.annualized_change_pct is None and t.volatility_annualized_pct is None
    assert t.moving_average_30 is None


def test_flat_series_has_no_rise_or_fall():
    t = compute_trend(series(dt.date(2024, 1, 1), *["0.83641"] * 25))
    assert t.biggest_rise is None and t.biggest_fall is None
    assert str(t.volatility_annualized_pct) == "0.0000"


def test_tiny_rates_keep_their_precision():
    """Gold (XAU) per euro is ~0.0003: averages must not be rounded to 4 places."""
    t = compute_trend(series(dt.date(2024, 1, 1), *["0.00029946"] * 30))
    assert t.moving_average_30 == D("0.00029946")


@pytest.mark.parametrize(
    ("previous", "latest", "expected"),
    [
        # (1.1005 − 1.1034) / 1.1034 × 100 = −0.26282… → −0.2628
        ("1.1034", "1.1005", (D("-0.0029"), D("-0.2628"))),
        (None, "1.1005", (None, None)),
    ],
)
def test_day_change(previous, latest, expected):
    assert day_change(D(previous) if previous else None, D(latest)) == expected
