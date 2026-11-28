"""The "Ask your data" agent: askAgent, confirmAgentAction, cancelAgentAction (ADR 0008, 0009).

The agent runs here, inside the API process, in an ordinary (threadpool) route. Its tools call
this same API over HTTP. Conversations and proposed writes live in PostgreSQL, so a restart
never loses a pending action, and a client only ever sends ids and questions.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from psycopg import Connection
from psycopg_pool import ConnectionPool

from fx_explorer.agent.loop import Agent, AgentResult
from fx_explorer.agent.sources import label_for_request
from fx_explorer.api.deps import (
    agent_repository,
    alert_repository,
    currency_repository,
    get_agent,
    get_pool,
    idempotency_repository,
    rate_repository,
)
from fx_explorer.api.errors import ProblemError, count_detail, field_error
from fx_explorer.api.models import (
    AgentAnswerOut,
    AgentAskIn,
    AgentPendingActionOut,
    AgentSourceOut,
    AgentStepOut,
    AgentUsageOut,
    AlertOut,
)
from fx_explorer.api.routes.alerts import _location, create_alert_idempotently
from fx_explorer.repositories.agent import AgentAction, AgentRepository, ConversationChangedError
from fx_explorer.repositories.alerts import AlertRepository
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.idempotency import IdempotencyRepository
from fx_explorer.repositories.rates import RateRepository

router = APIRouter(tags=["Agent"])

MAX_QUESTIONS_PER_CONVERSATION = 20  # the whole history is re-sent to the model every time


def _notice(action: AgentAction) -> str:
    """Tell the model what the user decided about an alert it proposed (it can't see the app)."""
    if action.status == "confirmed":
        a = action.input
        return (f"[App notice: the user confirmed and the alert was created: {a['base']}/{a['quote']} "
                f"{a['condition']} {a['threshold']}, id {action.alert_id}]\n")
    return f"[App notice: the user cancelled the proposed alert {action.id}; nothing was created]\n"


def _answer(conversation_id: UUID, result: AgentResult) -> AgentAnswerOut:
    pending = result.pending_action
    cost = None if result.cost_usd is None else f"{result.cost_usd:.6f}"
    return AgentAnswerOut(
        conversation_id=conversation_id,
        answer=result.answer,
        status=result.status,  # type: ignore[arg-type]  # "error" never gets here: it is a 503
        sources=[AgentSourceOut(label=label_for_request(s), request=s) for s in result.sources],
        steps=[
            AgentStepOut(
                step=s.step, model_text=s.model_text,
                tool_calls=[vars(c) for c in s.tool_calls],  # type: ignore[misc]
                input_tokens=s.input_tokens, output_tokens=s.output_tokens, duration_ms=s.model_duration_ms,
            )
            for s in result.steps
        ],
        pending_action=None if pending is None else AgentPendingActionOut(
            id=UUID(pending.id), tool="create_alert", status="pending", alert=pending.input),  # type: ignore[arg-type]
        usage=AgentUsageOut(
            input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
            cost_usd=cost, duration_ms=result.duration_ms,
        ),
    )


@router.post("/v1/agent/ask", response_model=AgentAnswerOut)
def ask_agent(
    body: AgentAskIn,
    agent: Agent = Depends(get_agent),
    repo: AgentRepository = Depends(agent_repository),
) -> AgentAnswerOut:
    conversation = None
    prefix = ""
    told: list[UUID] = []
    if body.conversation_id is not None:
        conversation = repo.get_conversation(body.conversation_id)
        if conversation is None:
            raise ProblemError("not-found", f"No conversation with id '{body.conversation_id}'.")
        if conversation.question_count >= MAX_QUESTIONS_PER_CONVERSATION:
            raise ProblemError("validation-error", count_detail(1, "field"), [field_error(
                "conversation_id",
                f"This conversation already has {MAX_QUESTIONS_PER_CONVERSATION} questions. Start a new one.")])
        decisions = repo.undelivered_decisions(conversation.id)
        prefix = "".join(_notice(a) for a in decisions)
        told = [a.id for a in decisions]

    # The slow part (seconds): the model and the tools. No database lock is held meanwhile.
    result = agent.run(prefix + body.question, history=conversation.messages if conversation else None)
    if result.status == "error":  # the model could not be used (after retries): nothing is saved
        raise ProblemError("service-unavailable", result.error or "The AI model is unavailable right now.")

    pending = result.pending_action
    try:
        conversation_id = repo.save_turn(
            conversation_id=conversation.id if conversation else None,
            expected_version=conversation.version if conversation else None,
            messages=result.messages,
            new_action=None if pending is None else (pending.id, pending.tool, pending.input),
            notified_action_ids=told,
        )
    except ConversationChangedError:
        raise ProblemError(
            "conflict",
            "This conversation was changed by another request while the agent was working. Ask again.",
        ) from None
    return _answer(conversation_id, result)


@router.post("/v1/agent/actions/{id}/confirm", status_code=201, response_model=AlertOut)
def confirm_action(
    id: UUID,
    response: Response,
    repo: AgentRepository = Depends(agent_repository),
    alerts: AlertRepository = Depends(alert_repository),
    currencies: CurrencyRepository = Depends(currency_repository),
    rates: RateRepository = Depends(rate_repository),
    keys: IdempotencyRepository = Depends(idempotency_repository),
    pool: ConnectionPool = Depends(get_pool),
) -> Any:
    action = repo.get_action(id)
    if action is None:
        raise ProblemError("not-found", f"No agent action with id '{id}'.")
    if action.status == "cancelled":
        raise ProblemError("conflict", "This action was cancelled, so it can no longer be confirmed. Ask the agent again.")

    def mark_confirmed(conn: Connection, alert: AlertOut) -> None:
        # Same transaction as the alert insert. Locking the row makes a concurrent cancel wait,
        # then see 'confirmed' (409), so "alert exists" and "action confirmed" never disagree.
        if repo.lock_status(conn, id) == "cancelled":
            raise ProblemError("conflict", "This action was cancelled, so it can no longer be confirmed.")
        repo.mark_confirmed(conn, id, alert.id)

    # The action id is the Idempotency-Key: confirming twice replays the stored 201 (ADR 0006).
    result = create_alert_idempotently(
        action.input, str(id), alerts=alerts, currencies=currencies, rates=rates, keys=keys,
        pool=pool, in_transaction=mark_confirmed,
    )
    if isinstance(result, AlertOut):
        response.headers["Location"] = _location(result.id)
    return result


@router.post("/v1/agent/actions/{id}/cancel", status_code=204)
def cancel_action(id: UUID, repo: AgentRepository = Depends(agent_repository)) -> Response:
    status = repo.cancel(id)
    if status is None:
        raise ProblemError("not-found", f"No agent action with id '{id}'.")
    if status == "confirmed":
        raise ProblemError(
            "conflict", "This action was already confirmed, so the alert exists. Delete it with DELETE /v1/alerts/{id}."
        )
    return Response(status_code=204)
