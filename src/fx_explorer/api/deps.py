"""Dependencies: small functions FastAPI calls to give each route what it needs.

A route asks for a repository with `repo: HealthRepository = Depends(health_repository)`.
FastAPI calls the function, which builds the repository on the app's shared connection pool.
Tests build the app with their own pool, so routes never know which database they use.
"""

from fastapi import Request
from psycopg_pool import ConnectionPool

from fx_explorer.repositories.alerts import AlertRepository
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.health import HealthRepository
from fx_explorer.repositories.idempotency import IdempotencyRepository
from fx_explorer.repositories.import_runs import ImportRunRepository
from fx_explorer.repositories.rates import RateRepository


def get_pool(request: Request) -> ConnectionPool:
    return request.app.state.pool


def health_repository(request: Request) -> HealthRepository:
    return HealthRepository(get_pool(request))


def currency_repository(request: Request) -> CurrencyRepository:
    return CurrencyRepository(get_pool(request))


def rate_repository(request: Request) -> RateRepository:
    return RateRepository(get_pool(request))


def alert_repository(request: Request) -> AlertRepository:
    return AlertRepository(get_pool(request))


def idempotency_repository(request: Request) -> IdempotencyRepository:
    return IdempotencyRepository(get_pool(request))


def import_run_repository(request: Request) -> ImportRunRepository:
    return ImportRunRepository(get_pool(request))
