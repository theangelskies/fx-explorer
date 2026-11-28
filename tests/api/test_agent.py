"""The agent endpoints (askAgent, confirmAgentAction, cancelAgentAction).

The agent runs on a fake model, so these tests cost nothing and need no network. They use the
real app on the real test database, like the other API tests."""

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from fx_explorer.agent.loop import Agent
from fx_explorer.agent.model import (
    FakeModel, ModelError, RuleBasedFakeModel, text_response, tool_response,
)
from fx_explorer.agent.prompts import CurrencyContext
from fx_explorer.agent.tools import ToolBox
from fx_explorer.api.app import create_app
from fx_explorer.config import get_settings
from fx_explorer.repositories.agent import AgentRepository
from fx_explorer.repositories.alerts import AlertRepository
from tests.api.conftest import seed

pytestmark = pytest.mark.integration

CREATE = {"base": "EUR", "quote": "USD", "condition": "above", "threshold": "1.20"}


def make_agent(model, handler=None) -> Agent:
    client = httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler or (lambda r: httpx.Response(404))))
    toolbox = ToolBox("http://api.test", client=client)
    currencies = CurrencyContext(toolbox)
    currencies._text, currencies._fetched_at = "  - USD (US Dollar), latest stored date 2026-11-17", float("inf")
    return Agent(model, toolbox, max_steps=5, currencies=currencies)


@pytest.fixture
def make_client(pool):
    seed(pool)
    clients = []

    def build(model=None, handler=None) -> TestClient:
        app = create_app(pool=pool, agent=make_agent(model or RuleBasedFakeModel(), handler))
        c = TestClient(app)
        clients.append(c)
        return c

    yield build
    for c in clients:
        c.close()


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def repo(pool):
    return AgentRepository(pool)


def ask(client, question="How has EUR to NGN moved?", conversation_id=None):
    return client.post("/v1/agent/ask", json={"question": question, "conversation_id": conversation_id})


def propose_alert(client) -> dict:
    resp = ask(client, "Alert me if EUR/USD goes above 1.20")
    assert resp.status_code == 200, resp.text
    return resp.json()


def problem(resp, status, type_):
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["type"] == f"/problems/{type_}"
    return resp.json()


# ------------------------------------------------------------------ ask


def test_ask_starts_a_conversation_and_returns_the_documented_shape(client, repo):
    resp = ask(client)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"conversation_id", "answer", "status", "sources", "steps", "pending_action", "usage"}
    assert body["status"] == "answered" and "fake answer" in body["answer"]
    assert body["pending_action"] is None and body["sources"] == []
    assert body["steps"][0]["step"] == 1 and body["steps"][0]["tool_calls"] == []
    assert body["usage"]["cost_usd"] is None                     # fake-model: no known price
    conversation = repo.get_conversation(uuid.UUID(body["conversation_id"]))
    assert [m["role"] for m in conversation.messages] == ["user", "assistant"]


def test_a_follow_up_continues_the_same_conversation(client, repo):
    first = ask(client, "one").json()
    second = ask(client, "two", first["conversation_id"]).json()
    assert second["conversation_id"] == first["conversation_id"]
    conversation = repo.get_conversation(uuid.UUID(first["conversation_id"]))
    assert conversation.version == 2 and conversation.question_count == 2
    assert len(conversation.messages) == 4


def test_tool_steps_and_sources_are_reported(make_client):
    def api(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/movers"
        return httpx.Response(200, json={"base": "EUR", "data": []})

    model = FakeModel([tool_response(("get_movers", {"base": "EUR"}), text="Checking."), text_response("Nothing moved.")])
    body = ask(make_client(model, api)).json()
    assert body["sources"] == [{"label": "EUR movers", "request": "GET /v1/movers?base=EUR"}]
    call = body["steps"][0]["tool_calls"][0]
    assert call["tool"] == "get_movers" and call["input"] == {"base": "EUR"} and call["status"] == 200
    assert call["request"] == "GET /v1/movers?base=EUR" and call["is_error"] is False
    assert body["steps"][0]["model_text"] == "Checking." and body["usage"]["input_tokens"] == 200


@pytest.mark.parametrize(
    ("payload", "field", "message"),
    [
        ({"question": ""}, "question", "Must not be empty."),
        ({"question": "   "}, "question", "Must contain some text."),
        ({"question": "x" * 1001}, "question", "Must be at most 1000 characters."),
        ({}, "question", "This field is required."),
        ({"question": "hi", "conversation_id": "not-a-uuid"}, "conversation_id", "Must be a valid UUID."),
        ({"question": "hi", "colour": "red"}, "colour", "Unknown field."),
    ],
)
def test_bad_questions_are_422_with_a_field_message(client, repo, payload, field, message):
    body = problem(client.post("/v1/agent/ask", json=payload), 422, "validation-error")
    assert {"field": field, "message": message} in body["errors"]


def test_a_question_of_exactly_1000_characters_is_fine(client):
    assert ask(client, "x" * 1000).status_code == 200


def test_an_unknown_conversation_is_404(client):
    problem(ask(client, "hi", str(uuid.uuid4())), 404, "not-found")


def test_a_full_conversation_is_422(client, repo):
    messages = [m for i in range(20) for m in ({"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": [{"type": "text", "text": "a"}]})]
    conversation_id = repo.save_turn(conversation_id=None, expected_version=None, messages=messages)
    body = problem(ask(client, "one more", str(conversation_id)), 422, "validation-error")
    assert body["errors"][0]["field"] == "conversation_id"


def test_model_failure_is_503_and_nothing_is_saved(make_client, pool):
    model = FakeModel([ModelError("The AI model is unavailable right now (HTTP 503) after 4 attempts.")])
    body = problem(ask(make_client(model)), 503, "service-unavailable")
    assert "unavailable right now" in body["detail"]
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM agent_conversations").fetchone()[0] == 0


def test_a_conversation_changed_meanwhile_is_409(make_client, repo):
    first = ask(make_client(), "one").json()
    conversation_id = uuid.UUID(first["conversation_id"])

    class BusyModel:
        """While the model 'thinks', another request saves the same conversation."""
        name = "busy"

        def complete(self, system, messages, tools):
            current = repo.get_conversation(conversation_id)
            repo.save_turn(conversation_id=conversation_id, expected_version=current.version, messages=current.messages)
            return text_response("late answer")

    problem(ask(make_client(BusyModel()), "two", str(conversation_id)), 409, "conflict")
    assert repo.get_conversation(conversation_id).question_count == 1   # the late answer was NOT saved


# ------------------------------------------------------------------ pending actions


def test_an_alert_request_proposes_but_creates_nothing(client, pool, repo):
    body = propose_alert(client)
    action = body["pending_action"]
    assert action["status"] == "pending" and action["tool"] == "create_alert"
    assert action["alert"] == {**CREATE, "note": None}
    assert body["steps"][0]["tool_calls"][0]["status"] == "pending"
    assert repo.get_action(uuid.UUID(action["id"])).status == "pending"
    assert AlertRepository(pool).count() == 0


def test_confirm_creates_the_alert_once(client, pool, repo):
    action_id = propose_alert(client)["pending_action"]["id"]
    resp = client.post(f"/v1/agent/actions/{action_id}/confirm")
    assert resp.status_code == 201
    alert = resp.json()
    assert (alert["base"], alert["quote"], alert["condition"], alert["threshold"]) == ("EUR", "USD", "above", "1.20")
    assert resp.headers["location"] == f"/v1/alerts/{alert['id']}" and "idempotent-replayed" not in resp.headers
    stored = repo.get_action(uuid.UUID(action_id))
    assert stored.status == "confirmed" and str(stored.alert_id) == alert["id"]
    assert AlertRepository(pool).count() == 1
    assert client.get(f"/v1/alerts/{alert['id']}").status_code == 200


def test_confirming_twice_returns_the_same_alert_and_creates_no_second(client, pool):
    action_id = propose_alert(client)["pending_action"]["id"]
    first = client.post(f"/v1/agent/actions/{action_id}/confirm")
    second = client.post(f"/v1/agent/actions/{action_id}/confirm")
    assert second.status_code == 201 and second.json()["id"] == first.json()["id"]
    assert second.headers["idempotent-replayed"] == "true"
    assert AlertRepository(pool).count() == 1


def test_the_action_id_is_the_idempotency_key(client, pool):
    action_id = propose_alert(client)["pending_action"]["id"]
    created = client.post(f"/v1/agent/actions/{action_id}/confirm").json()
    # the very same request sent straight to POST /v1/alerts with that key is a replay
    replay = client.post("/v1/alerts", json={**CREATE, "note": None}, headers={"Idempotency-Key": action_id})
    assert replay.status_code == 201 and replay.json()["id"] == created["id"]
    assert replay.headers["idempotent-replayed"] == "true"


def test_cancel_then_confirm_is_409_and_nothing_is_created(client, pool, repo):
    action_id = propose_alert(client)["pending_action"]["id"]
    assert client.post(f"/v1/agent/actions/{action_id}/cancel").status_code == 204
    problem(client.post(f"/v1/agent/actions/{action_id}/confirm"), 409, "conflict")
    assert repo.get_action(uuid.UUID(action_id)).status == "cancelled" and AlertRepository(pool).count() == 0


def test_cancelling_twice_is_fine(client):
    action_id = propose_alert(client)["pending_action"]["id"]
    assert client.post(f"/v1/agent/actions/{action_id}/cancel").status_code == 204
    resp = client.post(f"/v1/agent/actions/{action_id}/cancel")
    assert resp.status_code == 204 and resp.content == b""


def test_confirm_then_cancel_is_409_and_the_alert_stays(client, pool):
    action_id = propose_alert(client)["pending_action"]["id"]
    client.post(f"/v1/agent/actions/{action_id}/confirm")
    problem(client.post(f"/v1/agent/actions/{action_id}/cancel"), 409, "conflict")
    assert AlertRepository(pool).count() == 1


@pytest.mark.parametrize("verb", ["confirm", "cancel"])
def test_unknown_action_is_404_and_a_bad_id_is_400(client, verb):
    problem(client.post(f"/v1/agent/actions/{uuid.uuid4()}/{verb}"), 404, "not-found")
    problem(client.post(f"/v1/agent/actions/not-a-uuid/{verb}"), 400, "invalid-parameters")


def test_confirm_that_fails_validation_is_422_leaves_the_action_pending_and_can_be_retried(client, pool, repo):
    # BTC has no stored rates in the seed data, so an alert on EUR/BTC could never trigger.
    action_id = uuid.uuid4()
    conversation_id = repo.save_turn(
        conversation_id=None, expected_version=None, messages=[],
        new_action=(str(action_id), "create_alert", {**CREATE, "quote": "BTC", "note": None}),
    )
    resp = client.post(f"/v1/agent/actions/{action_id}/confirm")
    body = problem(resp, 422, "validation-error")
    assert body["errors"][0]["field"] == "quote"
    assert repo.get_action(action_id).status == "pending" and AlertRepository(pool).count() == 0
    # the key was released, so the same request is evaluated again (and fails the same way), not 409
    problem(client.post(f"/v1/agent/actions/{action_id}/confirm"), 422, "validation-error")
    assert conversation_id


# ------------------------------------------------------------------ the model is told what the user decided


def test_the_next_question_carries_an_app_notice_exactly_once(make_client):
    model = FakeModel([
        tool_response(("create_alert", CREATE)), text_response("Please confirm."),   # question 1
        text_response("Anything else?"),                                             # question 2
        text_response("Sure."),                                                      # question 3
    ])
    client = make_client(model)
    first = ask(client, "Alert me above 1.20").json()
    client.post(f"/v1/agent/actions/{first['pending_action']['id']}/confirm")
    ask(client, "thanks", first["conversation_id"])
    ask(client, "and one more", first["conversation_id"])

    second_question = model.calls[2]["messages"][-1]["content"]
    assert second_question.startswith("[App notice: the user confirmed and the alert was created: EUR/USD above 1.20, id ")
    assert second_question.endswith("thanks")
    assert model.calls[3]["messages"][-1]["content"] == "and one more"          # told once, not again


def test_a_cancel_notice_is_sent_too(make_client):
    model = FakeModel([tool_response(("create_alert", CREATE)), text_response("Confirm?"), text_response("OK.")])
    client = make_client(model)
    first = ask(client, "alert please").json()
    client.post(f"/v1/agent/actions/{first['pending_action']['id']}/cancel")
    ask(client, "never mind", first["conversation_id"])
    assert "the user cancelled the proposed alert" in model.calls[2]["messages"][-1]["content"]


# ------------------------------------------------------------------ how the app builds its agent


def test_without_an_injected_agent_a_missing_key_is_a_503(pool, monkeypatch):
    from fx_explorer.api import deps

    settings = get_settings().model_copy(update={"agent_provider": "anthropic", "anthropic_api_key": None, "agent_fake": False})
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    with TestClient(create_app(pool=pool)) as c:
        body = problem(ask(c), 503, "service-unavailable")
    assert "ANTHROPIC_API_KEY is not set" in body["detail"]


def test_agent_fake_setting_gives_a_working_agent_with_no_key(pool, monkeypatch):
    from fx_explorer.api import deps

    settings = get_settings().model_copy(update={"agent_fake": True, "anthropic_api_key": None, "openrouter_api_key": None})
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    with TestClient(create_app(pool=pool)) as c:
        assert "fake answer" in ask(c).json()["answer"]
        assert problem(ask(c, "[fake-error]"), 503, "service-unavailable")


# ------------------------------------------------------------------ double clicks, for real


def test_simultaneous_confirms_create_exactly_one_alert(client, pool):
    import threading

    action_id = propose_alert(client)["pending_action"]["id"]
    app = client.app
    statuses: list[int] = []

    def click():
        with TestClient(app) as c:
            statuses.append(c.post(f"/v1/agent/actions/{action_id}/confirm").status_code)

    threads = [threading.Thread(target=click) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(statuses) <= {201, 409}                       # a 409 here = "still being processed"
    assert 201 in statuses
    assert AlertRepository(pool).count() == 1                # never two
    assert client.post(f"/v1/agent/actions/{action_id}/confirm").status_code == 201  # and now a clean replay
