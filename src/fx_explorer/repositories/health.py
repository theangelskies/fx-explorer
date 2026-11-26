"""Database health check. Kept here so that even `SELECT 1` follows the rule: SQL only in repositories."""

from psycopg_pool import ConnectionPool


class HealthRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def ping(self) -> None:
        """Run a trivial query. Raises (psycopg.OperationalError or PoolTimeout) if the DB is unreachable."""
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")
