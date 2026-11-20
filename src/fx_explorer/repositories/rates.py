"""exchange_rates table. SQL lives here and nowhere else."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from psycopg_pool import ConnectionPool

from fx_explorer.domain.models import ExchangeRate
from fx_explorer.repositories.results import UpsertCounts

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
