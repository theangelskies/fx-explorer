"""Dependencies: small functions FastAPI calls to give each route what it needs.

A route asks for a repository with `repo: HealthRepository = Depends(health_repository)`.
FastAPI calls the function, which builds the repository on the app's shared connection pool.
Tests build the app with their own pool, so routes never know which database they use.
"""

from fastapi import Request
from psycopg_pool import ConnectionPool

from fx_explorer.repositories.health import HealthRepository


def get_pool(request: Request) -> ConnectionPool:
    return request.app.state.pool


def health_repository(request: Request) -> HealthRepository:
    return HealthRepository(get_pool(request))
