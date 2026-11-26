"""GET /v1/import-runs/latest (getLatestImportRun): data freshness (ADR 0007)."""

from fastapi import APIRouter, Depends

from fx_explorer.api.deps import import_run_repository
from fx_explorer.api.errors import ProblemError
from fx_explorer.api.models import ImportRunOut
from fx_explorer.repositories.import_runs import ImportRunRepository

router = APIRouter(tags=["Imports"])


@router.get("/v1/import-runs/latest", response_model=ImportRunOut)
def get_latest_import_run(runs: ImportRunRepository = Depends(import_run_repository)) -> ImportRunOut:
    run = runs.latest()
    if run is None:
        raise ProblemError("not-found", "No import has run yet. Run: uv run python -m fx_explorer import --incremental")
    # Only the contract's fields: the run's stored params stay internal.
    return ImportRunOut(**{field: run[field] for field in ImportRunOut.model_fields})
