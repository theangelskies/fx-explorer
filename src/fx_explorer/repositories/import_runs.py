"""import_runs table: one row per import, so we can see what happened and when."""

import json
from typing import Any, Literal

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

RunStatus = Literal["running", "success", "partial", "failed"]


class ImportRunRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def start(self, params: dict[str, Any]) -> int:
        """Record that a run began (status 'running'). Returns the run's id."""
        # default=str turns dates etc. into text so they can be stored as JSON
        payload = Jsonb(params, dumps=lambda obj: json.dumps(obj, default=str))
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO import_runs (status, params) VALUES ('running', %s) RETURNING id",
                (payload,),
            ).fetchone()
        return row[0]

    def finish(
        self,
        run_id: int,
        status: RunStatus,
        fetched: int = 0,
        inserted: int = 0,
        updated: int = 0,
        rejected: int = 0,
        error_message: str | None = None,
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE import_runs
                SET finished_at = now(), status = %s, fetched = %s, inserted = %s,
                    updated = %s, rejected = %s, error_message = %s
                WHERE id = %s
                """,
                (status, fetched, inserted, updated, rejected, error_message, run_id),
            )

    def get(self, run_id: int) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM import_runs WHERE id = %s", (run_id,))
            return cur.fetchone()

    def latest(self) -> dict[str, Any] | None:
        """The run that started most recently (any status), or None if nothing ever ran."""
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM import_runs ORDER BY started_at DESC, id DESC LIMIT 1")
            return cur.fetchone()
