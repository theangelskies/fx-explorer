"""PostgreSQL connection pool.

A pool keeps a few connections open and lends them out, which is much cheaper than
opening a new connection for every query.
"""

from psycopg_pool import ConnectionPool

from fx_explorer.config import get_settings


def create_pool(
    database_url: str | None = None,
    max_size: int = 5,
    *,
    wait: bool = True,
    timeout: float = 30.0,
) -> ConnectionPool:
    """Open a pool. Defaults to DATABASE_URL from .env; tests pass their own URL.

    wait=True (the importer, the tests): block until a connection works, and fail fast with
    a clear error if the database is unreachable.
    wait=False (the API): return at once and connect in the background, so the API can start
    and report "database down" (503) instead of refusing to start at all.
    timeout: how long pool.connection() waits for a free, working connection before raising
    PoolTimeout.

    Always close it when finished: pool.close()
    """
    url = database_url or get_settings().database_url
    pool = ConnectionPool(url, min_size=1, max_size=max_size, open=False, timeout=timeout)
    if wait:
        pool.open(wait=True, timeout=10)
    else:
        pool.open(wait=False)
    return pool
