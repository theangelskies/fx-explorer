"""agent_conversations and agent_actions (the "Ask your data" agent, ADR 0009)

Revision ID: 003
Revises: 002
Create Date: 2026-11-28

Plain SQL on purpose, like the other migrations.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "003"
down_revision: str | Sequence[str] | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One row per conversation. `messages` is the history the agent needs to continue (append-only:
    # the model's earlier turns must come back unchanged). `version` lets two requests that
    # touch the same conversation notice each other (optimistic concurrency).
    op.execute(
        """
        CREATE TABLE agent_conversations (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            messages   JSONB NOT NULL DEFAULT '[]'::jsonb,
            version    INT NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # A write the agent PROPOSED. Nothing happens until a person confirms. `id` is the id the
    # agent created, and is used as the Idempotency-Key when the alert is created.
    op.execute(
        """
        CREATE TABLE agent_actions (
            id              UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES agent_conversations (id) ON DELETE CASCADE,
            tool            TEXT NOT NULL CHECK (tool IN ('create_alert')),
            input           JSONB NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'confirmed', 'cancelled')),
            alert_id        UUID NULL,          -- set when confirmed (no FK: the alert may be deleted later)
            notified        BOOLEAN NOT NULL DEFAULT false,  -- has the model been told about the decision?
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_agent_actions_conversation ON agent_actions (conversation_id, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE agent_actions")
    op.execute("DROP TABLE agent_conversations")
