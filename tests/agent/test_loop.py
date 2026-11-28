"""Agent loop tests. FakeModel plays the model, respx fakes our API: no cost, no network."""

import json

import httpx
import pytest
import respx

from fx_explorer.agent.loop import Agent
from fx_explorer.agent.model import FakeModel, ModelError, ModelResponse, Usage, text_response, tool_response
from fx_explorer.agent.prompts import CurrencyContext
from fx_explorer.agent.tools import ToolBox

from .test_tools import PAIR, problem

API = "http://api.test"
CREATE = {"base": "EUR", "quote": "USD", "condition": "above", "threshold": "1.20"}


@pytest.fixture
def api():
    with respx.mock(base_url=API, assert_all_called=False) as router:
        yield router


@pytest.fixture
def toolbox():
    box = ToolBox(API, timeout_seconds=1)
    yield box
    box.close()


def agent_with(model, toolbox, max_steps=8):
    # a fixed currency list: no HTTP call to list_currencies in these tests
    context = CurrencyContext(toolbox)
    context._text, context._fetched_at = "  - NGN (Nigerian Naira), latest stored date 2026-11-25", 10**12
    return Agent(model, toolbox, max_steps=max_steps, currencies=context)


def every_tool_use_is_answered(messages):
    """The API rejects history where a tool_use has no tool_result. Check the whole thread."""
    wanted, answered = set(), set()
    for m in messages:
        if isinstance(m["content"], list):
            for b in m["content"]:
                if b["type"] == "tool_use":
                    wanted.add(b["id"])
                if b["type"] == "tool_result":
                    answered.add(b["tool_use_id"])
    return wanted == answered


# ------------------------------------------------------------------ the happy paths


def test_one_tool_then_answer(api, toolbox):
    api.get("/v1/pairs/EUR/NGN").respond(200, json=PAIR)
    model = FakeModel([
        tool_response(("get_pair_trend", {"base": "EUR", "quote": "NGN", "window": "3y"}), text="Let me check."),
        text_response("EUR/NGN rose 67.76% over 3 years. Source: GET /v1/pairs/EUR/NGN?window=3y"),
    ])
    result = agent_with(model, toolbox).run("How has EUR/NGN moved over 3 years?")

    assert result.status == "answered" and "67.76%" in result.answer
    assert result.sources == ["GET /v1/pairs/EUR/NGN?window=3y"]
    assert len(result.steps) == 2 and len(model.calls) == 2
    assert result.steps[0].model_text == "Let me check."
    call = result.steps[0].tool_calls[0]
    assert (call.tool, call.status, call.is_error) == ("get_pair_trend", 200, False)
    assert call.request == "GET /v1/pairs/EUR/NGN?window=3y"
    assert result.pending_action is None
    assert every_tool_use_is_answered(result.messages)


def test_the_model_sees_the_tool_result_with_its_source_line(api, toolbox):
    api.get("/v1/pairs/EUR/NGN").respond(200, json=PAIR)
    model = FakeModel([
        tool_response(("get_pair_trend", {"base": "EUR", "quote": "NGN", "window": "3y"})),
        text_response("done"),
    ])
    agent_with(model, toolbox).run("q")
    second_call_messages = model.calls[1]["messages"]
    assert [m["role"] for m in second_call_messages] == ["user", "assistant", "user"]
    block = second_call_messages[2]["content"][0]
    assert block["type"] == "tool_result" and block["tool_use_id"] == "toolu_0_0"
    assert block["content"].startswith("GET /v1/pairs/EUR/NGN?window=3y\n{")
    assert json.loads(block["content"].split("\n", 1)[1])["trend"]["change_pct"] == "67.7643"


def test_system_prompt_has_the_date_and_the_stored_currencies(api, toolbox):
    model = FakeModel([text_response("hi")])
    agent_with(model, toolbox).run("q")
    system = model.calls[0]["system"]
    assert "NGN (Nigerian Naira)" in system and "Today is 20" in system
    assert {t["name"] for t in model.calls[0]["tools"]} >= {"get_pair_trend", "create_alert"}


def test_multiple_tools_in_one_step_and_over_several_steps(api, toolbox):
    api.get("/v1/pairs/EUR/NGN").respond(200, json=PAIR)
    api.get("/v1/pairs/EUR/USD").respond(200, json={**PAIR, "quote": "USD"})
    api.get("/v1/movers").respond(200, json={"base": "EUR", "data": []})
    model = FakeModel([
        # step 1: two tools at once (parallel tool use)
        tool_response(("get_pair_trend", {"base": "EUR", "quote": "NGN", "window": "1y"}),
                      ("get_pair_trend", {"base": "EUR", "quote": "USD", "window": "1y"})),
        # step 2: one more tool
        tool_response(("get_movers", {"base": "EUR"}), n=1),
        text_response("NGN moved most."),
    ])
    result = agent_with(model, toolbox).run("Which moved most?")

    assert result.status == "answered" and len(result.steps) == 3
    assert [len(s.tool_calls) for s in result.steps] == [2, 1, 0]
    # both results of step 1 travel back in ONE user message
    step1_results = model.calls[1]["messages"][2]["content"]
    assert [b["tool_use_id"] for b in step1_results] == ["toolu_0_0", "toolu_0_1"]
    assert result.sources == [
        "GET /v1/pairs/EUR/NGN?window=1y", "GET /v1/pairs/EUR/USD?window=1y", "GET /v1/movers?base=EUR",
    ]
    assert every_tool_use_is_answered(result.messages)


def test_tool_error_then_recovery(api, toolbox):
    api.get("/v1/pairs/EUR/XYZ").mock(return_value=problem(404, "Not found", "No rates stored for EUR/XYZ."))
    api.get("/v1/pairs/EUR/NGN").respond(200, json=PAIR)
    model = FakeModel([
        tool_response(("get_pair_trend", {"base": "EUR", "quote": "XYZ"})),
        tool_response(("get_pair_trend", {"base": "EUR", "quote": "NGN", "window": "3y"}), n=1),
        text_response("EUR/NGN rose."),
    ])
    result = agent_with(model, toolbox).run("q")

    error_block = model.calls[1]["messages"][2]["content"][0]
    assert error_block["is_error"] is True and "Error 404: No rates stored for EUR/XYZ." in error_block["content"]
    first, second = result.steps[0].tool_calls[0], result.steps[1].tool_calls[0]
    assert first.is_error and first.status == 404 and not second.is_error
    assert result.status == "answered"
    assert result.sources == ["GET /v1/pairs/EUR/NGN?window=3y"]  # the failed request is not a source


def test_a_crashing_tool_becomes_an_error_result_not_a_crash(toolbox, monkeypatch):
    monkeypatch.setattr(toolbox, "run", lambda name, tool_input: 1 / 0)
    model = FakeModel([tool_response(("get_movers", {})), text_response("Sorry, the tool failed.")])
    result = agent_with(model, toolbox).run("q")
    assert result.status == "answered"
    assert model.calls[1]["messages"][2]["content"][0]["is_error"] is True


# ------------------------------------------------------------------ the step limit


def test_max_steps_reached(api, toolbox):
    api.get("/v1/movers").respond(200, json={"base": "EUR", "data": []})
    model = FakeModel([tool_response(("get_movers", {}), n=i) for i in range(3)])  # never answers
    result = agent_with(model, toolbox, max_steps=3).run("q")

    assert result.status == "max_steps"
    assert result.answer.startswith("I couldn't finish within 3 steps. Here's what I found so far:")
    assert "GET /v1/movers?base=EUR" in result.answer
    assert len(model.calls) == 3                     # exactly max_steps model calls, no more
    assert len(api.calls) == 2                       # the 3rd step's tool was NOT run
    assert every_tool_use_is_answered(result.messages)  # history stays valid for a follow-up


# ------------------------------------------------------------------ the safety rule: no writes


def test_create_alert_is_intercepted_into_a_pending_action(api, toolbox):
    model = FakeModel([
        tool_response(("create_alert", CREATE)),
        text_response("I can create this alert: EUR/USD above 1.20. Please confirm."),
    ])
    result = agent_with(model, toolbox).run("Alert me if EUR/USD goes above 1.20")

    assert len(api.calls) == 0                                   # nothing was sent to the API
    action = result.pending_action
    assert action is not None and action.status == "pending"
    assert action.input == {**CREATE, "note": None}
    call = result.steps[0].tool_calls[0]
    assert call.status == "pending" and call.request is None
    assert result.sources == []
    told_to_model = model.calls[1]["messages"][2]["content"][0]["content"]
    assert "NOT EXECUTED" in told_to_model
    assert result.status == "answered"


def test_a_second_proposal_in_the_same_run_is_refused(api, toolbox):
    other = {**CREATE, "quote": "GBP", "threshold": "0.90"}
    model = FakeModel([tool_response(("create_alert", CREATE), ("create_alert", other)), text_response("ok")])
    result = agent_with(model, toolbox).run("q")
    assert result.pending_action.input["quote"] == "USD"
    blocks = model.calls[1]["messages"][2]["content"]
    assert "NOT EXECUTED" in blocks[0]["content"]
    assert blocks[1]["is_error"] is True and "already waiting" in blocks[1]["content"]
    assert len(api.calls) == 0


# ------------------------------------------------------------------ usage, cost, failures, follow-ups


def test_tokens_and_cost_are_added_up(api, toolbox):
    api.get("/v1/movers").respond(200, json={"base": "EUR", "data": []})
    model = FakeModel([
        tool_response(("get_movers", {})),   # 100 in / 30 out
        text_response("ok", input_tokens=200, output_tokens=50),
    ])
    model.name = "claude-sonnet-5-5"          # a model we know the price of
    result = agent_with(model, toolbox).run("q")
    assert (result.usage.input_tokens, result.usage.output_tokens) == (300, 80)
    assert result.cost_usd == pytest.approx((300 * 2.00 + 80 * 10.00) / 1_000_000)
    assert [s.input_tokens for s in result.steps] == [100, 200]


def test_unknown_model_has_no_cost(toolbox):
    assert agent_with(FakeModel([text_response("hi")]), toolbox).run("q").cost_usd is None


def test_model_failure_gives_a_clear_message_and_no_crash(toolbox):
    model = FakeModel([ModelError("The AI model is unavailable right now (RateLimitError) after 4 attempts.")])
    result = agent_with(model, toolbox).run("q")
    assert result.status == "error" and "unavailable right now" in result.answer
    assert "Nothing was changed" in result.answer


def test_a_refusal_is_reported_not_treated_as_an_answer(toolbox):
    refusal = ModelResponse(content=[], stop_reason="refusal", usage=Usage(), model="fake")
    result = agent_with(FakeModel([refusal]), toolbox).run("q")
    assert result.status == "refused"


def test_a_cut_off_answer_says_so(toolbox):
    cut = ModelResponse(content=[{"type": "text", "text": "EUR/NGN rose"}], stop_reason="max_tokens")
    assert "cut off" in agent_with(FakeModel([cut]), toolbox).run("q").answer


def test_follow_up_questions_keep_the_history_and_never_edit_it(api, toolbox):
    api.get("/v1/movers").respond(200, json={"base": "EUR", "data": []})
    model = FakeModel([tool_response(("get_movers", {})), text_response("First."), text_response("Second.")])
    agent = agent_with(model, toolbox)
    first = agent.run("one")
    snapshot = json.dumps(first.messages)
    second = agent.run("two", history=first.messages)

    assert second.answer == "Second."
    assert json.dumps(second.messages[: len(first.messages)]) == snapshot   # append-only
    assert second.messages[len(first.messages)] == {"role": "user", "content": "two"}
    assert [m["role"] for m in model.calls[2]["messages"]] == ["user", "assistant", "user", "assistant", "user"]


def test_the_transcript_is_json_and_saved(api, toolbox, tmp_path):
    api.get("/v1/movers").respond(200, json={"base": "EUR", "data": []})
    model = FakeModel([tool_response(("get_movers", {})), text_response("ok")])
    result = agent_with(model, toolbox).run("what moved?")
    path = result.transcript.save(tmp_path, result.to_dict())
    saved = json.loads(path.read_text())
    assert saved["question"] == "what moved?" and len(saved["steps"]) == 2
    assert saved["steps"][0]["tool_calls"][0]["request"] == "GET /v1/movers?base=EUR"
    assert saved["result"]["status"] == "answered"


def test_a_proposal_from_a_run_that_later_failed_is_dropped(api, toolbox):
    """The model proposed an alert at step 1, then failed at step 2: the user never saw the proposal."""
    model = FakeModel([tool_response(("create_alert", CREATE)), ModelError("The AI model is unavailable right now.")])
    result = agent_with(model, toolbox).run("Alert me above 1.20")
    assert result.status == "error" and result.pending_action is None
