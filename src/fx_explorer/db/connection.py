"""PostgreSQL connection pool.

A pool keeps a few connections open and lends them out, which is much cheaper than
opening a new connection for every query.
"""

from psycopg_pool import ConnectionPool

from fx_explorer.config import get_settings


def create_pool(database_url: str | None = None, max_size: int = 5) -> ConnectionPool:
    """Open a pool. Defaults to DATABASE_URL from .env; tests pass their own URL.

    Always close it when finished: pool.close()
    """
    url = database_url or get_settings().database_url
    pool = ConnectionPool(url, min_size=1, max_size=max_size, open=False)
    pool.open(wait=True, timeout=10)  # fail fast with a clear error if the DB is unreachable
    return pool
