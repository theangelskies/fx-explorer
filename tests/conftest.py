"""Shared test setup. The integration tests use a REAL PostgreSQL: the fx_explorer_test database."""

from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from fx_explorer.config import get_settings
from fx_explorer.db.connection import create_pool

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    settings = get_settings()
    url = settings.test_database_url
    # Safety: these tests wipe tables. Never let them touch the real database.
    if url == settings.database_url or not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.exit("TEST_DATABASE_URL must be a different database whose name ends in _test", 2)
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError:
        pytest.skip("Test database unreachable. Start it with: docker compose up -d")
    return url


@pytest.fixture(scope="session")
def migrated_db(test_database_url: str) -> str:
    """Build the schema from scratch with Alembic (downgrade then upgrade = clean slate)."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", test_database_url.replace("%", "%%"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return test_database_url


@pytest.fixture
def pool(migrated_db: str):
    """A pool on the test database, with all tables emptied before each test."""
    pool = create_pool(migrated_db)
    with pool.connection() as conn:
        conn.execute("TRUNCATE exchange_rates, currencies, import_runs, rate_alerts, idempotency_keys RESTART IDENTITY CASCADE")
    yield pool
    pool.close()
