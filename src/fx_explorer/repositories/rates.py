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
class PairStats:
    min: Decimal
    max: Decimal
    avg: Decimal  # NOT rounded here; how many decimals to show is the API's decision


@dataclass(frozen=True)
class PairWindow:
    """Everything the pair-detail view needs, read in one go."""

    latest: RatePoint
    previous: RatePoint | None  # the stored rate just before `latest`
    series: list[RatePoint]  # oldest first, within the window
    stats: PairStats | None  # None only if series is empty


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

    def pair_window(self, base: str, quote: str, days: int) -> PairWindow | None:
        """Latest + previous rate, and the series/stats for the `days` calendar days that end
        at the latest stored date (inclusive). None if the pair has no stored rates."""
        point = class_row(RatePoint)
        pair = {"base": base, "quote": quote}
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
            window = {**pair, "start": latest.rate_date - timedelta(days=days - 1),
                      "end": latest.rate_date}
            in_window = (
                "FROM exchange_rates WHERE base = %(base)s AND quote = %(quote)s "
                "AND rate_date BETWEEN %(start)s AND %(end)s"
            )
            series = cur.execute(
                f"SELECT rate_date, rate {in_window} ORDER BY rate_date ASC", window
            ).fetchall()
        # The window always contains `latest`, so series is never empty in practice.
        stats = None
        if series:
            rates = [p.rate for p in series]
            # Decimal arithmetic: exact sums; the division keeps 28 significant digits.
            stats = PairStats(min(rates), max(rates), sum(rates) / len(rates))
        return PairWindow(
            latest=latest,
            previous=last_two[1] if len(last_two) > 1 else None,
            series=series,
            stats=stats,
        )
