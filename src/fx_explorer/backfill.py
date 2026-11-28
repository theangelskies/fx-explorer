"""Planning a multi-year backfill. Pure functions: no HTTP, no database.

A backfill imports years of history in yearly chunks, e.g. --from 2016-01-01 becomes one
request for 2016, one for 2017, ... and one for the (partial) current year. Small chunks keep
each API response a reasonable size, and if one year fails only that year needs a re-run.

Every currency has its own first available date (`start_date` from Frankfurter's
/currencies). We never ask for dates before it: inside a chunk, each quote starts at
max(chunk start, quote's start_date, base's start_date). Quotes that end up with a later
start than the others get their own request for that chunk.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlannedRequest:
    """One GET /rates call."""

    year: int
    start: dt.date
    end: dt.date
    quotes: tuple[str, ...]

    def describe(self, base: str) -> str:
        return (f"GET /rates?base={base}&quotes={','.join(self.quotes)}"
                f"&from={self.start}&to={self.end}")


@dataclass(frozen=True)
class BackfillPlan:
    base: str
    start: dt.date
    end: dt.date
    requests: list[PlannedRequest]
    # Human-readable reasons why a currency's range was shortened or it was left out.
    limits: list[str] = field(default_factory=list)

    @property
    def years(self) -> list[int]:
        return sorted({r.year for r in self.requests})


def year_chunks(start: dt.date, end: dt.date) -> list[tuple[int, dt.date, dt.date]]:
    """Split start..end (inclusive) into calendar years: [(2016, 2016-01-01, 2016-12-31), ...].

    The first and last chunks are partial when start/end fall inside a year.
    """
    if start > end:
        raise ValueError("start must not be after end")
    return [
        (year, max(start, dt.date(year, 1, 1)), min(end, dt.date(year, 12, 31)))
        for year in range(start.year, end.year + 1)
    ]


def plan_backfill(
    base: str,
    quotes: Sequence[str],
    start: dt.date,
    end: dt.date,
    start_dates: Mapping[str, dt.date | None],
) -> BackfillPlan:
    """Work out every request a backfill will make.

    `start_dates` maps currency code -> its first available date (None = unknown). A code
    missing from the mapping is not a known currency.
    """
    limits: list[str] = []
    if base not in start_dates:
        limits.append(f"{base} (base) is not in the currencies table: nothing to import.")
        return BackfillPlan(base, start, end, [], limits)

    base_start = start_dates[base]
    floor = start
    if base_start is not None and base_start > start:
        floor = base_start
        limits.append(f"{base} (base) has data only from {base_start}: every quote starts there.")

    # Earliest date we may request for each quote.
    quote_from: dict[str, dt.date] = {}
    for q in dict.fromkeys(quotes):  # dict.fromkeys: drop duplicates, keep order
        if q == base:
            limits.append(f"{q} skipped: it is the base currency.")
            continue
        if q not in start_dates:
            limits.append(f"{q} skipped: not in the currencies table (unknown currency).")
            continue
        q_start = start_dates[q]
        first = floor
        if q_start is None:
            limits.append(f"{q}: start_date unknown, requesting from {floor} unchanged.")
        elif q_start > floor:
            first = q_start
            if q_start > end:
                limits.append(f"{q} skipped: data starts {q_start}, after the range end {end}.")
                continue
            limits.append(f"{q} limited: data starts {q_start} (its start_date), not {floor}.")
        quote_from[q] = first

    requests: list[PlannedRequest] = []
    for year, chunk_start, chunk_end in year_chunks(start, end):
        # Group this year's quotes by the date they may start from.
        groups: dict[dt.date, list[str]] = {}
        for q, first in quote_from.items():
            if first > chunk_end:
                continue  # not available yet in this year
            groups.setdefault(max(chunk_start, first), []).append(q)
        for group_start in sorted(groups):
            requests.append(PlannedRequest(year, group_start, chunk_end, tuple(groups[group_start])))

    return BackfillPlan(base, start, end, requests, limits)
