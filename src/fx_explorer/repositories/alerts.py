"""rate_alerts table. SQL lives here and nowhere else.

`current_rate` and `triggered` are not stored: they are worked out in SQL from the latest
stored rate every time an alert is read, so they are always up to date after an import.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from psycopg import Connection
from psycopg.rows import class_row
from psycopg_pool import ConnectionPool

from fx_explorer.repositories.results import KeysetPage

Condition = Literal["above", "below"]


@dataclass(frozen=True)
class Alert:
    id: UUID
    base: str
    quote: str
    condition: Condition
    threshold: Decimal
    note: str | None
    created_at: datetime
    current_rate: Decimal | None  # latest stored rate for the pair, None if there is none
    triggered: bool


# The LATERAL subquery finds the newest stored rate of each alert's pair, using the index
# ix_exchange_rates_base_quote_date. "above" triggers at rate >= threshold, "below" at
# rate <= threshold (the contract defines reaching the threshold as triggered).
# With no current rate the comparison is NULL, and coalesce turns that into false.
_ALERTS = """
SELECT * FROM (
    SELECT ra.id, ra.base, ra.quote, ra.condition, ra.threshold, ra.note, ra.created_at,
           lr.rate AS current_rate,
           coalesce(CASE ra.condition
                        WHEN 'above' THEN lr.rate >= ra.threshold
                        ELSE lr.rate <= ra.threshold
                    END, false) AS triggered
    FROM rate_alerts ra
    LEFT JOIN LATERAL (
        SELECT rate FROM exchange_rates er
        WHERE er.base = ra.base AND er.quote = ra.quote
        ORDER BY er.rate_date DESC
        LIMIT 1
    ) lr ON true
) a
"""


class AlertRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create(
        self,
        *,
        base: str,
        quote: str,
        condition: Condition,
        threshold: Decimal,
        note: str | None = None,
        conn: Connection | None = None,
    ) -> Alert:
        """Insert an alert and return it with current_rate/triggered filled in.

        Pass `conn` to run inside a caller's transaction (the idempotency code does this so
        that the alert and the stored response are saved together, or not at all).
        """
        if conn is not None:
            return self._create(conn, base, quote, condition, threshold, note)
        with self._pool.connection() as own:
            return self._create(own, base, quote, condition, threshold, note)

    def _create(self, conn, base, quote, condition, threshold, note) -> Alert:
        new_id = conn.execute(
            "INSERT INTO rate_alerts (base, quote, condition, threshold, note) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (base, quote, condition, threshold, note),
        ).fetchone()[0]
        with conn.cursor(row_factory=class_row(Alert)) as cur:
            return cur.execute(_ALERTS + " WHERE a.id = %s", (new_id,)).fetchone()

    def get(self, alert_id: UUID) -> Alert | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=class_row(Alert)) as cur:
            return cur.execute(_ALERTS + " WHERE a.id = %s", (alert_id,)).fetchone()

    def list_page(
        self,
        *,
        limit: int,
        triggered: bool | None = None,
        after: tuple[datetime, UUID] | None = None,
    ) -> KeysetPage[Alert]:
        """Alerts newest first (created_at DESC, id DESC).

        `after` is the (created_at, id) of the last alert on the previous page. Both columns
        sort in the same direction, so a single row comparison expresses "strictly after".
        """
        where: list[str] = []
        params: dict[str, object] = {"limit": limit + 1}
        if triggered is not None:
            where.append("a.triggered = %(triggered)s")
            params["triggered"] = triggered
        if after is not None:
            where.append("(a.created_at, a.id) < (%(after_ts)s, %(after_id)s)")
            params["after_ts"], params["after_id"] = after
        sql = _ALERTS
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY a.created_at DESC, a.id DESC LIMIT %(limit)s"
        with self._pool.connection() as conn, conn.cursor(row_factory=class_row(Alert)) as cur:
            rows = cur.execute(sql, params).fetchall()
        return KeysetPage.from_rows(rows, limit)

    def delete(self, alert_id: UUID) -> bool:
        """True if an alert was deleted, False if there was none with this id."""
        with self._pool.connection() as conn:
            cur = conn.execute("DELETE FROM rate_alerts WHERE id = %s", (alert_id,))
            return cur.rowcount == 1

    def count(self) -> int:
        with self._pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM rate_alerts").fetchone()[0]
