"""GET /v1/health (operationId: getHealth)."""

from fastapi import APIRouter, Depends

from fx_explorer.api.deps import health_repository
from fx_explorer.api.models import Health
from fx_explorer.repositories.health import HealthRepository

router = APIRouter()


@router.get("/v1/health", response_model=Health)
def get_health(repo: HealthRepository = Depends(health_repository)) -> Health:
    # If the database is down, ping() raises and errors.py turns that into a 503 Problem.
    repo.ping()
    return Health(status="ok", database="ok")
