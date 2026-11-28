"""FastAPI application factory.

Run in development:  uv run uvicorn fx_explorer.api.app:app --reload

There is exactly ONE contract: the hand-written openapi.yaml in the repo root
(docs/decisions/0001). FastAPI's auto-generated spec and docs pages are switched off; we serve
our file at /openapi.yaml and point Swagger UI (/docs) at it.

The API layer reads through repositories only: no SQL here, and never a call to Frankfurter.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse
from psycopg_pool import ConnectionPool

from fx_explorer.api.errors import register_error_handlers
from fx_explorer.api.routes import alerts, currencies, health, import_runs, movers, pairs, rates
from fx_explorer.db.connection import create_pool

# src/fx_explorer/api/app.py -> parents[3] is the repository root.
CONTRACT_PATH = Path(__file__).resolve().parents[3] / "openapi.yaml"


def create_app(pool: ConnectionPool | None = None) -> FastAPI:
    """Build the app.

    pool=None (normal use): open a pool on DATABASE_URL at startup, close it at shutdown.
    pool given (tests): use it as is; whoever created it closes it.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if pool is not None:
            app.state.pool = pool
            yield
            return
        # wait=False: start even if the DB is down (health then reports 503).
        # timeout=5: a request waits at most 5 s for a DB connection before giving up.
        own_pool = create_pool(max_size=10, wait=False, timeout=5)
        app.state.pool = own_pool
        try:
            yield
        finally:
            own_pool.close()

    app = FastAPI(
        title="fx-explorer API",
        lifespan=lifespan,
        openapi_url=None,  # no auto-generated /openapi.json ...
        docs_url=None,  # ... and no auto-generated /docs or /redoc: ours replace them
        redoc_url=None,
    )
    if pool is not None:
        app.state.pool = pool  # also available when a test client skips the lifespan

    register_error_handlers(app)

    @app.get("/openapi.yaml", include_in_schema=False)
    def contract() -> FileResponse:
        return FileResponse(CONTRACT_PATH, media_type="application/yaml")

    @app.get("/docs", include_in_schema=False)
    def swagger_ui() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/openapi.yaml", title="fx-explorer API docs")

    modules = (health, currencies, rates, pairs, movers, import_runs, alerts)
    for module in modules:
        app.include_router(module.router)
    # The API's routes, kept for errors.py to build a complete Allow header on 405s.
    app.state.api_routes = [r for m in modules for r in m.router.routes]
    return app


app = create_app()
