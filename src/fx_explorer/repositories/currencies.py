"""Currencies table. SQL lives here and nowhere else."""

from collections.abc import Sequence

from psycopg_pool import ConnectionPool

from fx_explorer.domain.models import Currency
from fx_explorer.repositories.results import UpsertCounts

# (xmax = 0) is true for a brand-new row and false for a row that was updated.
# See rates.py for the full explanation.
_UPSERT_SQL = """
INSERT INTO currencies (iso_code, iso_numeric, name, symbol, type, start_date, end_date)
VALUES (%(iso_code)s, %(iso_numeric)s, %(name)s, %(symbol)s, %(type)s, %(start_date)s, %(end_date)s)
ON CONFLICT (iso_code) DO UPDATE SET
    iso_numeric = EXCLUDED.iso_numeric,
    name        = EXCLUDED.name,
    symbol      = EXCLUDED.symbol,
    type        = EXCLUDED.type,
    start_date  = EXCLUDED.start_date,
    end_date    = EXCLUDED.end_date,
    updated_at  = now()
WHERE (currencies.iso_numeric, currencies.name, currencies.symbol, currencies.type,
       currencies.start_date, currencies.end_date)
      IS DISTINCT FROM
      (EXCLUDED.iso_numeric, EXCLUDED.name, EXCLUDED.symbol, EXCLUDED.type,
       EXCLUDED.start_date, EXCLUDED.end_date)
RETURNING (xmax = 0) AS inserted
"""


class CurrencyRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert_many(self, currencies: Sequence[Currency]) -> UpsertCounts:
        """Insert new currencies, update changed ones, leave identical ones alone."""
        if not currencies:
            return UpsertCounts()
        params = [c.model_dump() for c in currencies]
        inserted = updated = 0
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, params, returning=True)
            while True:
                row = cur.fetchone()  # None when this statement changed nothing
                if row is not None:
                    inserted, updated = (inserted + 1, updated) if row[0] else (inserted, updated + 1)
                if not cur.nextset():
                    break
        return UpsertCounts(inserted, updated, len(params) - inserted - updated)

    def known_codes(self) -> set[str]:
        with self._pool.connection() as conn:
            return {row[0] for row in conn.execute("SELECT iso_code FROM currencies")}

    def count(self) -> int:
        with self._pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM currencies").fetchone()[0]
