"""idempotency_keys table: storage for the Idempotency-Key pattern. SQL only, no HTTP.

The rules (what to do with a replay, a mismatch, a key in progress) live in
api/idempotency.py (ADR 0006). This module only stores and reads keys safely when two
requests arrive at the same moment.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from psycopg import Connection
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

KeyStatus = Literal["in_progress", "completed"]


@dataclass(frozen=True)
class IdempotencyRecord:
    key: str
    request_hash: str
    status: KeyStatus
    response_status: int | None
    response_body: Any  # the stored JSON, already decoded (dict/list), or None
    created_at: datetime


@dataclass(frozen=True)
class Claim:
    """Result of trying to claim a key.

    claimed=True:  the key was new; it is now ours, marked in_progress.
    claimed=False: someone used it before; `existing` says how far they got.
    """

    claimed: bool
    existing: IdempotencyRecord | None = None


class IdempotencyRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def claim(self, key: str, request_hash: str, stale_after_seconds: float = 60) -> Claim:
        """Atomically claim `key` for this request, or return what is already stored.

        INSERT ... ON CONFLICT is atomic: if two requests with the same key arrive together,
        the primary key guarantees exactly one of them gets the row; the other gets no row
        back and reads the winner's record instead.

        Stale keys: if a server crashed mid-request, its key would stay 'in_progress' forever
        and every retry would get 409. So an 'in_progress' key older than
        `stale_after_seconds` may be taken over (the ON CONFLICT ... DO UPDATE ... WHERE).
        Completed keys are never taken over.
        """
        # The loop covers one rare race: the existing row is released (deleted) between our
        # INSERT and our SELECT. Then we simply try to claim it again.
        for _ in range(3):
            with self._pool.connection() as conn:
                inserted = conn.execute(
                    "INSERT INTO idempotency_keys (key, request_hash, status) "
                    "VALUES (%s, %s, 'in_progress') "
                    "ON CONFLICT (key) DO UPDATE "
                    "  SET request_hash = EXCLUDED.request_hash, created_at = now() "
                    "  WHERE idempotency_keys.status = 'in_progress' "
                    "    AND idempotency_keys.created_at < now() - make_interval(secs => %s) "
                    "RETURNING key",
                    (key, request_hash, stale_after_seconds),
                ).fetchone()
                if inserted is not None:
                    return Claim(claimed=True)
                existing = self._get(conn, key)
                if existing is not None:
                    return Claim(claimed=False, existing=existing)
        raise RuntimeError(f"could not claim idempotency key {key!r}")

    def complete(
        self,
        key: str,
        response_status: int,
        response_body: Any,
        conn: Connection | None = None,
    ) -> None:
        """Store the response for replays and mark the key completed.

        Pass `conn` to save it in the same transaction as the work itself.
        """
        sql = (
            "UPDATE idempotency_keys SET status = 'completed', response_status = %s, "
            "response_body = %s WHERE key = %s"
        )
        params = (response_status, Jsonb(response_body), key)
        if conn is not None:
            conn.execute(sql, params)
            return
        with self._pool.connection() as own:
            own.execute(sql, params)

    def release(self, key: str) -> None:
        """Forget an in-progress key (the request failed), so the client can retry with it."""
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM idempotency_keys WHERE key = %s AND status = 'in_progress'", (key,)
            )

    def get(self, key: str) -> IdempotencyRecord | None:
        with self._pool.connection() as conn:
            return self._get(conn, key)

    @staticmethod
    def _get(conn: Connection, key: str) -> IdempotencyRecord | None:
        with conn.cursor(row_factory=class_row(IdempotencyRecord)) as cur:
            return cur.execute(
                "SELECT key, request_hash, status, response_status, response_body, created_at "
                "FROM idempotency_keys WHERE key = %s",
                (key,),
            ).fetchone()
