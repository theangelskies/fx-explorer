"""exchange_rates table. SQL lives here and nowhere else."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from psycopg.rows import class_row
from psycopg_pool import ConnectionPool

from fx_explorer.domain.models import ExchangeRate
from fx_explorer.repositories.results import KeysetPage, UpsertCounts

logger = logging.getLogger(__name__)

# How the counts work
# -------------------
# "ON CONFLICT DO UPDATE ... WHERE rate IS DISTINCT FROM new rate" gives three outcomes per row:
#   * new row                  -> row is inserted        -> RETURNING gives a row
#   * existing, rate changed   -> row is updated         -> RETURNING gives a row
#   * existing, same rate      -> the WHERE is false, nothing is written -> RETURNING gives NO row
# To tell "inserted" from "updated" we return (xmax = 0). xmax is a hidden system column
# on every PostgreSQL row; it is 0 on a row that was just inserted and non-zero on a row that
# was updated by ON CONFLICT. This is a well-known PostgreSQL idiom but not an officially
# documented guarantee, so the integration tests check it. "Unchanged" is whatever is left over.
_UPSERT_SQL = """
INSERT INTO exchange_rates (rate_date, base, quote, rate)
VALUES (%(rate_date)s, %(base)s, %(quote)s, %(rate)s)
ON CONFLICT (rate_date, base, quote) DO UPDATE
    SET rate = EXCLUDED.rate, updated_at = now()
    WHERE exchange_rates.rate IS DISTINCT FROM EXCLUDED.rate
RETURNING (xmax = 0) AS inserted
"""


@dataclass(frozen=True)
class RateUpsertResult:
    counts: UpsertCounts
    # Rates whose base or quote is not in the currencies table. They were NOT written.
    unknown_currency: list[ExchangeRate] = field(default_factory=list)


@dataclass(frozen=True)
class StoredRate:
    """One row of exchange_rates, as the API's Rate shows it."""

    rate_date: date
    base: str
    quote: str
    rate: Decimal
    updated_at: datetime


@dataclass(frozen=True)
class RatePoint:
    rate_date: date
    rate: Decimal


@dataclass(frozen=True)
class PairHistory:
    """Everything the pair-detail view needs, read in one go. Lists are oldest first."""

    latest: RatePoint
    previous: RatePoint | None  # the stored rate just before `latest`
    first_available_date: date  # earliest stored rate for the pair
    window_start: date  # first date the window asks for
    points: list[RatePoint]  # every daily rate in window_start..latest
    recent: list[RatePoint]  # the last (up to) 200 rates up to latest, for moving averages
    series: list[RatePoint]  # chart points: daily, or last rate per week/month


@dataclass(frozen=True)
class LatestMove:
    """A quote's latest stored rate and the stored rate just before it (for listMovers)."""

    quote: str
    rate_date: date
    rate: Decimal
    previous_rate_date: date
    previous_rate: Decimal


# date_trunc units for down-sampling. Fixed strings only: they are put into the SQL text
# (DISTINCT ON and ORDER BY must contain the exact same expression), never user input.
_TRUNC_UNITS = {"week": "week", "month": "month"}  # PostgreSQL weeks are ISO: Monday-Sunday

class RateRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert_many(self, rates: Sequence[ExchangeRate]) -> RateUpsertResult:
        """Write a batch of rates in ONE transaction (all saved, or none if something breaks).

        A rate that points at a currency we don't have would violate the foreign key and
        abort the whole batch. So we check first, skip those rates, and report them back.
        """
        if not rates:
            return RateUpsertResult(UpsertCounts())

        inserted = updated = 0
        with self._pool.connection() as conn:  # commits when the block ends, rolls back on error
            wanted = {code for r in rates for code in (r.base, r.quote)}
            known = {
                row[0]
                for row in conn.execute(
                    "SELECT iso_code FROM currencies WHERE iso_code = ANY(%s)", (list(wanted),)
                )
            }
            good = [r for r in rates if r.base in known and r.quote in known]
            skipped = [r for r in rates if r.base not in known or r.quote not in known]
            for r in skipped:
                logger.warning("Skipping %s %s->%s: currency not in the currencies table",
                               r.rate_date, r.base, r.quote)

            with conn.cursor() as cur:
                cur.executemany(_UPSERT_SQL, [r.model_dump() for r in good], returning=True)
                while True:
                    row = cur.fetchone()  # None when this statement changed nothing
                    if row is not None:
                        if row[0]:
                            inserted += 1
                        else:
                            updated += 1
                    if not cur.nextset():
                        break

        unchanged = len(good) - inserted - updated
        return RateUpsertResult(UpsertCounts(inserted, updated, unchanged), skipped)

    def latest_date(self, base: str) -> date | None:
        """Newest rate_date stored for this base currency, or None if there is none yet."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT max(rate_date) FROM exchange_rates WHERE base = %s", (base,)
            ).fetchone()
        return row[0]

    def count(self) -> int:
        with self._pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM exchange_rates").fetchone()[0]

    def list_page(
        self,
        *,
        base: str,
        limit: int,
        quotes: Sequence[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        newest_first: bool = True,
        after: tuple[date, str] | None = None,
    ) -> KeysetPage[StoredRate]:
        """Rates for one base, ordered by date (newest or oldest first), then quote A->Z.

        `after` is the (rate_date, quote) of the last row of the previous page: the cursor.
        The order is total (one row per date+quote for a given base), so pages never overlap
        or skip a row.
        """
        where = ["base = %(base)s"]
        params: dict[str, object] = {"base": base, "limit": limit + 1}
        if quotes:
            where.append("quote = ANY(%(quotes)s)")
            params["quotes"] = list(quotes)
        if date_from is not None:
            where.append("rate_date >= %(from)s")
            params["from"] = date_from
        if date_to is not None:
            where.append("rate_date <= %(to)s")
            params["to"] = date_to
        if after is not None:
            # "Strictly after the last row I saw": a later position in date order, or the
            # same date with a later quote (the tie-breaker is always A->Z).
            # The first part (rate_date <= / >= ...) is implied by the second, but spelling
            # it out lets PostgreSQL use it as an index condition and jump straight to the
            # cursor position instead of scanning from the first row.
            op, op_eq = ("<", "<=") if newest_first else (">", ">=")
            where.append(
                f"rate_date {op_eq} %(after_date)s "
                f"AND (rate_date {op} %(after_date)s OR quote > %(after_quote)s)"
            )
            params["after_date"], params["after_quote"] = after

        direction = "DESC" if newest_first else "ASC"
        sql = (
            "SELECT rate_date, base, quote, rate, updated_at FROM exchange_rates "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY rate_date {direction}, quote ASC LIMIT %(limit)s"
        )
        with self._pool.connection() as conn, conn.cursor(row_factory=class_row(StoredRate)) as cur:
            rows = cur.execute(sql, params).fetchall()
        return KeysetPage.from_rows(rows, limit)

    def has_pair(self, base: str, quote: str) -> bool:
        """Is at least one rate stored for this pair?"""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM exchange_rates WHERE base = %s AND quote = %s)",
                (base, quote),
            ).fetchone()
        return row[0]

    def pair_history(
        self, base: str, quote: str, days: int | None, interval: str = "day"
    ) -> PairHistory | None:
        """History for the pair-detail view. None if the pair has no stored rates.

        days: window length in calendar days ending at the latest stored date (inclusive);
              None = all stored history ("max").
        interval: "day" (every rate), "week" or "month" (last rate of each period) for `series`.
        """
        if interval not in ("day", *_TRUNC_UNITS):
            raise ValueError(f"unknown interval {interval!r}")
        pair = {"base": base, "quote": quote}
        point = class_row(RatePoint)
        with self._pool.connection() as conn, conn.cursor(row_factory=point) as cur:
            last_two = cur.execute(
                "SELECT rate_date, rate FROM exchange_rates "
                "WHERE base = %(base)s AND quote = %(quote)s "
                "ORDER BY rate_date DESC LIMIT 2",
                pair,
            ).fetchall()
            if not last_two:
                return None
            latest = last_two[0]
            first_date = conn.execute(
                "SELECT min(rate_date) FROM exchange_rates WHERE base = %(base)s AND quote = %(quote)s",
                pair,
            ).fetchone()[0]
            window_start = first_date if days is None else latest.rate_date - timedelta(days=days - 1)
            window = {**pair, "start": window_start, "end": latest.rate_date}
            in_window = ("FROM exchange_rates WHERE base = %(base)s AND quote = %(quote)s "
                         "AND rate_date BETWEEN %(start)s AND %(end)s")

            points = cur.execute(
                f"SELECT rate_date, rate {in_window} ORDER BY rate_date ASC", window
            ).fetchall()
            recent = cur.execute(
                "SELECT rate_date, rate FROM ("
                "  SELECT rate_date, rate FROM exchange_rates"
                "  WHERE base = %(base)s AND quote = %(quote)s AND rate_date <= %(end)s"
                "  ORDER BY rate_date DESC LIMIT 200"
                ") last_200 ORDER BY rate_date ASC",
                window,
            ).fetchall()
            if interval == "day":
                series = points
            else:
                # DISTINCT ON keeps the FIRST row of each group in ORDER BY order. Grouping by
                # week/month and sorting each group newest first keeps the LAST rate of each
                # period. The outer query puts the result back in date order.
                unit = _TRUNC_UNITS[interval]
                series = cur.execute(
                    "SELECT rate_date, rate FROM ("
                    f"  SELECT DISTINCT ON (date_trunc('{unit}', rate_date)) rate_date, rate"
                    f"  {in_window}"
                    f"  ORDER BY date_trunc('{unit}', rate_date), rate_date DESC"
                    ") per_period ORDER BY rate_date ASC",
                    window,
                ).fetchall()

        return PairHistory(
            latest=latest,
            previous=last_two[1] if len(last_two) > 1 else None,
            first_available_date=first_date,
            window_start=window_start,
            points=points,
            recent=recent,
            series=series,
        )

    def latest_moves(self, base: str) -> list[LatestMove]:
        """For every quote stored against `base` with at least two rates: its latest rate and
        the one before it. "Latest" is per quote (quotes aren't always updated on the same day).

        row_number() numbers each quote's rates newest first; rank 1 is the latest, rank 2 the
        previous one. This reads every rate of the base once (~23,000 rows today), which is fast;
        see ADR 0007 for the alternative if history grows a lot.
        """
        with self._pool.connection() as conn, conn.cursor(row_factory=class_row(LatestMove)) as cur:
            return cur.execute(
                """
                WITH ranked AS (
                    SELECT quote, rate_date, rate,
                           row_number() OVER (PARTITION BY quote ORDER BY rate_date DESC) AS rn
                    FROM exchange_rates
                    WHERE base = %s
                )
                SELECT latest.quote, latest.rate_date, latest.rate,
                       prev.rate_date AS previous_rate_date, prev.rate AS previous_rate
                FROM ranked latest
                JOIN ranked prev ON prev.quote = latest.quote AND prev.rn = 2
                WHERE latest.rn = 1
                ORDER BY latest.quote
                """,
                (base,),
            ).fetchall()
