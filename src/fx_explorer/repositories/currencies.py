"""Currencies table. SQL lives here and nowhere else."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from psycopg.rows import class_row
from psycopg_pool import ConnectionPool

from fx_explorer.domain.models import Currency
from fx_explorer.repositories.results import KeysetPage, UpsertCounts

CurrencyType = Literal["currency", "metal", "crypto"]


@dataclass(frozen=True)
class CurrencySummary:
    """A currency plus facts about its stored rates (what the API's Currency shows)."""

    iso_code: str
    name: str
    type: CurrencyType
    iso_numeric: str | None
    symbol: str | None
    start_date: date | None
    end_date: date | None
    rates_count: int  # rates where this currency is the base OR the quote
    latest_rate_date: date | None  # None when rates_count == 0


# Every currency row, plus its rate count and latest date. The two LATERAL subqueries run
# once per currency row: one counts rates where it is the base, one where it is the quote
# (base <> quote is enforced, so a rate is never counted twice). Both are answered from
# indexes (ix_exchange_rates_base_quote_date and ix_exchange_rates_quote_date).
# greatest() ignores NULLs, so a currency used only as a quote still gets a date.
_SUMMARY_SELECT = """
SELECT c.iso_code, c.name, c.type, c.iso_numeric, c.symbol, c.start_date, c.end_date,
       (b.n + q.n)::int                AS rates_count,
       greatest(b.latest, q.latest)    AS latest_rate_date
FROM currencies c
CROSS JOIN LATERAL (SELECT count(*) AS n, max(rate_date) AS latest
                    FROM exchange_rates WHERE base = c.iso_code) b
CROSS JOIN LATERAL (SELECT count(*) AS n, max(rate_date) AS latest
                    FROM exchange_rates WHERE quote = c.iso_code) q
"""

_HAS_RATES = """(EXISTS (SELECT 1 FROM exchange_rates WHERE base = c.iso_code)
             OR EXISTS (SELECT 1 FROM exchange_rates WHERE quote = c.iso_code))"""


def _like_pattern(text: str) -> str:
    """'%text%' for ILIKE, with ILIKE's own wildcards (% and _) escaped so they match literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"

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

    def list_page(
        self,
        *,
        limit: int,
        type_: CurrencyType | None = None,
        q: str | None = None,
        has_rates: bool | None = None,
        after_code: str | None = None,
    ) -> KeysetPage[CurrencySummary]:
        """Currencies ordered by iso_code, starting after `after_code` (the cursor position).

        Filters left as None are not applied. `q` matches code or name, case-insensitively.
        """
        # Only fixed SQL fragments are joined together; every value is a bound parameter.
        where: list[str] = []
        params: dict[str, object] = {"limit": limit + 1}
        if type_ is not None:
            where.append("c.type = %(type)s")
            params["type"] = type_
        if q is not None:
            where.append("(c.iso_code ILIKE %(pattern)s OR c.name ILIKE %(pattern)s)")
            params["pattern"] = _like_pattern(q)
        if has_rates is True:
            where.append(_HAS_RATES)
        elif has_rates is False:
            where.append(f"NOT {_HAS_RATES}")
        if after_code is not None:
            where.append("c.iso_code > %(after)s")
            params["after"] = after_code

        sql = _SUMMARY_SELECT
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY c.iso_code LIMIT %(limit)s"

        with self._pool.connection() as conn, conn.cursor(row_factory=class_row(CurrencySummary)) as cur:
            rows = cur.execute(sql, params).fetchall()
        return KeysetPage.from_rows(rows, limit)

    def get(self, code: str) -> CurrencySummary | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=class_row(CurrencySummary)) as cur:
            return cur.execute(_SUMMARY_SELECT + " WHERE c.iso_code = %s", (code,)).fetchone()

    def find_known(self, codes: Sequence[str]) -> set[str]:
        """Which of these codes exist in the currencies table."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT iso_code FROM currencies WHERE iso_code = ANY(%s)", (list(codes),)
            )
            return {row[0] for row in rows}

    def known_codes(self) -> set[str]:
        with self._pool.connection() as conn:
            return {row[0] for row in conn.execute("SELECT iso_code FROM currencies")}

    def count(self) -> int:
        with self._pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM currencies").fetchone()[0]
