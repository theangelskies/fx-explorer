"""create currencies, exchange_rates, import_runs

Revision ID: 001
Revises:
Create Date: 2026-11-21

Plain SQL on purpose (no ORM): what you read here is exactly what runs.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Reference table. Loaded first because exchange_rates points at it.
    op.execute(
        """
        CREATE TABLE currencies (
            iso_code    TEXT PRIMARY KEY,
            iso_numeric TEXT NULL,
            name        TEXT NOT NULL,
            symbol      TEXT NULL,
            type        TEXT NOT NULL CHECK (type IN ('currency', 'metal', 'crypto')),
            start_date  DATE NULL,
            end_date    DATE NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # One row per (day, base, quote). The primary key is what makes re-imports safe.
    op.execute(
        """
        CREATE TABLE exchange_rates (
            rate_date     DATE NOT NULL,
            base          TEXT NOT NULL REFERENCES currencies (iso_code),
            quote         TEXT NOT NULL REFERENCES currencies (iso_code),
            rate          NUMERIC NOT NULL CHECK (rate > 0),
            first_seen_at TIMESTAMPTZ DEFAULT now(),
            updated_at    TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (rate_date, base, quote),
            CHECK (base <> quote)
        )
        """
    )

    # A log of every import run, with its counts and outcome.
    op.execute(
        """
        CREATE TABLE import_runs (
            id            BIGSERIAL PRIMARY KEY,
            started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at   TIMESTAMPTZ NULL,
            status        TEXT NOT NULL
                          CHECK (status IN ('running', 'success', 'partial', 'failed')),
            params        JSONB NULL,
            fetched       INT NOT NULL DEFAULT 0,
            inserted      INT NOT NULL DEFAULT 0,
            updated       INT NOT NULL DEFAULT 0,
            rejected      INT NOT NULL DEFAULT 0,
            error_message TEXT NULL
        )
        """
    )


def downgrade() -> None:
    # Reverse order: exchange_rates depends on currencies, so it must go first.
    op.execute("DROP TABLE import_runs")
    op.execute("DROP TABLE exchange_rates")
    op.execute("DROP TABLE currencies")
