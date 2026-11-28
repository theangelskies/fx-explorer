"""Dependencies: small functions FastAPI calls to give each route what it needs.

A route asks for a repository with `repo: HealthRepository = Depends(health_repository)`.
FastAPI calls the function, which builds the repository on the app's shared connection pool.
Tests build the app with their own pool, so routes never know which database they use.
"""

import threading

from fastapi import Request
from psycopg_pool import ConnectionPool

from fx_explorer.agent.factory import build_agent
from fx_explorer.agent.loop import Agent
from fx_explorer.agent.model import ModelError
from fx_explorer.api.errors import ProblemError
from fx_explorer.config import get_settings
from fx_explorer.repositories.agent import AgentRepository
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


def agent_repository(request: Request) -> AgentRepository:
    return AgentRepository(get_pool(request))


_agent_lock = threading.Lock()


def get_agent(request: Request) -> Agent:
    """The one Agent of this app, built on first use (so the API starts without a model key).
    Tests (and AGENT_FAKE) get a fake model; a missing key becomes a 503 Problem."""
    state = request.app.state
    agent = getattr(state, "agent", None)
    if agent is None:
        with _agent_lock:
            agent = getattr(state, "agent", None)
            if agent is None:
                try:
                    agent = build_agent(get_settings())
                except ModelError as exc:  # e.g. no API key configured
                    raise ProblemError("service-unavailable", str(exc)) from exc
                state.agent = agent
                state.agent_owned = True  # we built it, so we close it at shutdown
    return agent
