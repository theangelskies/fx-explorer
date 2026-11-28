"""agent_conversations and agent_actions tables. SQL lives here and nowhere else (ADR 0009)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


class ConversationChangedError(Exception):
    """Another request saved this conversation while we were working on it."""


@dataclass(frozen=True)
class Conversation:
    id: UUID
    messages: list[dict[str, Any]]
    version: int

    @property
    def question_count(self) -> int:
        """How many questions the user has asked (a user message that is plain text)."""
        return sum(1 for m in self.messages if m["role"] == "user" and isinstance(m["content"], str))


@dataclass(frozen=True)
class AgentAction:
    id: UUID
    conversation_id: UUID
    tool: str
    input: dict[str, Any]
    status: str  # pending | confirmed | cancelled
    alert_id: UUID | None
    notified: bool
    created_at: datetime


class AgentRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # ------------------------------------------------------------ conversations
    def get_conversation(self, conversation_id: UUID) -> Conversation | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT id, messages, version FROM agent_conversations WHERE id = %s",
                (conversation_id,),
            ).fetchone()
        return None if row is None else Conversation(id=row[0], messages=row[1], version=row[2])

    def save_turn(
        self,
        *,
        conversation_id: UUID | None,
        expected_version: int | None,
        messages: list[dict[str, Any]],
        new_action: tuple[str, str, dict[str, Any]] | None = None,
        notified_action_ids: list[UUID] | None = None,
    ) -> UUID:
        """Save one answered question, atomically: the history, the proposed action (if any), and
        the 'the model has been told' flags.

        conversation_id None: create the conversation. Otherwise the row is updated ONLY if its
        version is still `expected_version`; if not, ConversationChangedError.
        new_action: (action id, tool, input).
        """
        with self._pool.connection() as conn:
            if conversation_id is None:
                conversation_id = conn.execute(
                    "INSERT INTO agent_conversations (messages) VALUES (%s) RETURNING id",
                    (Jsonb(messages),),
                ).fetchone()[0]
            else:
                updated = conn.execute(
                    "UPDATE agent_conversations SET messages = %s, version = version + 1, "
                    "updated_at = now() WHERE id = %s AND version = %s",
                    (Jsonb(messages), conversation_id, expected_version),
                )
                if updated.rowcount != 1:
                    raise ConversationChangedError
            if new_action is not None:
                action_id, tool, tool_input = new_action
                conn.execute(
                    "INSERT INTO agent_actions (id, conversation_id, tool, input) VALUES (%s, %s, %s, %s)",
                    (action_id, conversation_id, tool, Jsonb(tool_input)),
                )
            if notified_action_ids:
                conn.execute(
                    "UPDATE agent_actions SET notified = true WHERE id = ANY(%s)", (notified_action_ids,)
                )
        return conversation_id

    # ------------------------------------------------------------ actions
    def get_action(self, action_id: UUID) -> AgentAction | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute("SELECT * FROM agent_actions WHERE id = %s", (action_id,)).fetchone()
        return None if row is None else _action(row)

    def undelivered_decisions(self, conversation_id: UUID) -> list[AgentAction]:
        """Confirmed or cancelled actions the model has not been told about yet."""
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                "SELECT * FROM agent_actions WHERE conversation_id = %s AND status <> 'pending' "
                "AND NOT notified ORDER BY updated_at",
                (conversation_id,),
            ).fetchall()
        return [_action(r) for r in rows]

    def pending_for_conversation(self, conversation_id: UUID) -> list[AgentAction]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                "SELECT * FROM agent_actions WHERE conversation_id = %s AND status = 'pending' "
                "ORDER BY created_at",
                (conversation_id,),
            ).fetchall()
        return [_action(r) for r in rows]

    def lock_status(self, conn: Connection, action_id: UUID) -> str | None:
        """Lock the action row until the caller's transaction ends, and return its status.
        A concurrent cancel waits here, then sees the final status."""
        row = conn.execute(
            "SELECT status FROM agent_actions WHERE id = %s FOR UPDATE", (action_id,)
        ).fetchone()
        return None if row is None else row[0]

    def mark_confirmed(self, conn: Connection, action_id: UUID, alert_id: UUID) -> None:
        conn.execute(
            "UPDATE agent_actions SET status = 'confirmed', alert_id = %s, updated_at = now() WHERE id = %s",
            (alert_id, action_id),
        )

    def cancel(self, action_id: UUID) -> str | None:
        """Cancel a pending action. Returns the status afterwards ('cancelled'), the status that
        blocked it ('confirmed'), or None if there is no such action."""
        with self._pool.connection() as conn:
            status = self.lock_status(conn, action_id)
            if status == "pending":
                conn.execute(
                    "UPDATE agent_actions SET status = 'cancelled', updated_at = now() WHERE id = %s",
                    (action_id,),
                )
                return "cancelled"
            return status


def _action(row: dict[str, Any]) -> AgentAction:
    return AgentAction(
        id=row["id"], conversation_id=row["conversation_id"], tool=row["tool"], input=row["input"],
        status=row["status"], alert_id=row["alert_id"], notified=row["notified"], created_at=row["created_at"],
    )
