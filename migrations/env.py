"""Alembic environment: tells Alembic which database to connect to."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from fx_explorer.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# We write plain SQL migrations (no ORM models), so there is no metadata to compare against.
target_metadata = None


def get_url() -> str:
    """Use a URL set in code (the tests do this) or fall back to DATABASE_URL from .env."""
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    # Alembic talks to the database through SQLAlchemy, which needs to be told
    # to use the psycopg 3 driver: "postgresql+psycopg://".
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def run_migrations_offline() -> None:
    """Generate SQL text without connecting (alembic upgrade head --sql)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and run the migrations."""
    connectable = create_engine(get_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
