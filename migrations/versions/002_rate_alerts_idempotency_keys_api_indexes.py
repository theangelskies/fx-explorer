"""rate_alerts, idempotency_keys, and indexes for the API

Revision ID: 002
Revises: 001
Create Date: 2026-11-26

Plain SQL on purpose (no ORM): what you read here is exactly what runs.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "002"
down_revision: str | Sequence[str] | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Alerts created through POST /v1/alerts. The API validates everything first and returns
    # friendly 422s; these CHECKs are only a last line of defence against bugs.
    op.execute(
        """
        CREATE TABLE rate_alerts (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            base       TEXT NOT NULL REFERENCES currencies (iso_code),
            quote      TEXT NOT NULL REFERENCES currencies (iso_code),
            condition  TEXT NOT NULL CHECK (condition IN ('above', 'below')),
            -- scale() = number of decimal places of a NUMERIC value (contract: at most 10)
            threshold  NUMERIC NOT NULL CHECK (threshold > 0 AND scale(threshold) <= 10),
            note       TEXT NULL CHECK (char_length(note) <= 200),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (base <> quote)
        )
        """
    )
    # GET /v1/alerts pages newest first: ORDER BY created_at DESC, id DESC.
    op.execute("CREATE INDEX ix_rate_alerts_created_id ON rate_alerts (created_at DESC, id DESC)")

    # One row per Idempotency-Key seen on POST /v1/alerts (see ADR 0006).
    op.execute(
        """
        CREATE TABLE idempotency_keys (
            key             TEXT PRIMARY KEY CHECK (char_length(key) BETWEEN 1 AND 255),
            request_hash    TEXT NOT NULL,
            status          TEXT NOT NULL CHECK (status IN ('in_progress', 'completed')),
            response_status INT NULL,
            response_body   JSONB NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- a completed key must have a stored response to replay
            CHECK (status = 'in_progress' OR response_status IS NOT NULL)
        )
        """
    )

    # Indexes for the API's read queries. The primary key (rate_date, base, quote) starts
    # with the date, so it can't help queries that filter by currency first.
    #
    # 1. One pair's history, newest first: pair detail, "latest rate" for alerts.
    op.execute(
        "CREATE INDEX ix_exchange_rates_base_quote_date "
        "ON exchange_rates (base, quote, rate_date DESC)"
    )
    # 2. GET /v1/rates: WHERE base = ? ORDER BY rate_date DESC, quote ASC (keyset pagination).
    op.execute(
        "CREATE INDEX ix_exchange_rates_base_date_quote "
        "ON exchange_rates (base, rate_date DESC, quote)"
    )
    # 3. Per-currency counts / latest date when the currency is the QUOTE side
    #    (the base side is served by index 1).
    op.execute(
        "CREATE INDEX ix_exchange_rates_quote_date ON exchange_rates (quote, rate_date DESC)"
    )

    # The contract promises Rate.updated_at is always present. It always gets its default,
    # but 001 didn't forbid NULL, so make it official (backfilling any NULL first).
    op.execute(
        "UPDATE exchange_rates SET updated_at = coalesce(first_seen_at, now()) "
        "WHERE updated_at IS NULL"
    )
    op.execute("ALTER TABLE exchange_rates ALTER COLUMN updated_at SET NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE exchange_rates ALTER COLUMN updated_at DROP NOT NULL")
    op.execute("DROP INDEX ix_exchange_rates_quote_date")
    op.execute("DROP INDEX ix_exchange_rates_base_date_quote")
    op.execute("DROP INDEX ix_exchange_rates_base_quote_date")
    op.execute("DROP TABLE idempotency_keys")
    op.execute("DROP TABLE rate_alerts")  # also drops its index
