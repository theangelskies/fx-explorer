"""OpenRouterModel: format translation and retries. respx fakes OpenRouter: no network, no cost."""

import argparse
import json

import httpx
import pytest
import respx

from fx_explorer.agent import cli
from fx_explorer.agent.model import (
    ModelError, OpenRouterModel, Usage, estimate_cost_usd, from_openai_response, to_openai_messages,
)
from fx_explorer.agent.tools import TOOL_DEFINITIONS
from fx_explorer.config import get_settings

BASE = "http://or.test/api/v1"
SECRET = "sk-or-secret-123"


def completion(message, finish="stop", usage=None):
    return {"model": "vendor/model:free", "choices": [{"message": message, "finish_reason": finish}],
            "usage": usage or {"prompt_tokens": 120, "completion_tokens": 30}}


@pytest.fixture
def router():
    with respx.mock(base_url=BASE) as r:
        yield r


def model(max_retries=3):
    sleeps: list[float] = []
    m = OpenRouterModel("vendor/model:free", SECRET, base_url=BASE, max_retries=max_retries, sleep=sleeps.append)
    return m, sleeps


# ------------------------------------------------------------------ translation

HISTORY = [
    {"role": "user", "content": "How has EUR/NGN moved?"},
    {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "hmm", "signature": "x"},
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "call_1", "name": "get_pair_trend", "input": {"base": "EUR", "quote": "NGN"}},
        {"type": "tool_use", "id": "call_2", "name": "get_movers", "input": {}}]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "GET /v1/pairs/EUR/NGN\n{}"},
        {"type": "tool_result", "tool_use_id": "call_2", "content": "Error 503: down", "is_error": True}]},
]


def test_history_is_translated_to_openai_messages():
    out = to_openai_messages("SYS", HISTORY)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == {"role": "user", "content": "How has EUR/NGN moved?"}
    assistant = out[2]
    assert assistant["content"] == "Let me check." and [c["id"] for c in assistant["tool_calls"]] == ["call_1", "call_2"]
    assert assistant["tool_calls"][0]["function"] == {"name": "get_pair_trend", "arguments": '{"base": "EUR", "quote": "NGN"}'}
    assert out[3:] == [
        {"role": "tool", "tool_call_id": "call_1", "content": "GET /v1/pairs/EUR/NGN\n{}"},
        {"role": "tool", "tool_call_id": "call_2", "content": "Error 503: down"},
    ]


def test_an_assistant_turn_with_only_tool_calls_has_null_content():
    out = to_openai_messages("S", [HISTORY[0], {"role": "assistant", "content": [HISTORY[1]["content"][2]]}])
    assert out[2]["content"] is None and out[2]["tool_calls"][0]["id"] == "call_1"


def test_response_with_tool_calls_is_translated_back():
    data = completion({"content": None, "tool_calls": [
        {"id": "abc", "type": "function", "function": {"name": "get_movers", "arguments": '{"base": "EUR"}'}}]},
        finish="tool_calls", usage={"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 40}})
    r = from_openai_response(data, "m")
    assert r.content == [{"type": "tool_use", "id": "abc", "name": "get_movers", "input": {"base": "EUR"}}]
    assert r.stop_reason == "tool_use" and r.usage == Usage(60, 10, 40, 0)


def test_a_model_that_says_stop_but_asks_for_a_tool_still_runs_the_tool():
    data = completion({"content": "", "tool_calls": [{"id": "a", "function": {"name": "get_movers", "arguments": "{}"}}]})
    assert from_openai_response(data, "m").stop_reason == "tool_use"


def test_garbled_arguments_become_empty_input_and_missing_ids_are_filled():
    data = completion({"tool_calls": [{"function": {"name": "get_pair_trend", "arguments": "{not json"}}]}, "tool_calls")
    block = from_openai_response(data, "m").content[0]
    assert block["input"] == {} and block["id"] == "call_0"


@pytest.mark.parametrize(("finish", "stop"), [("stop", "end_turn"), ("length", "max_tokens"), ("content_filter", "refusal")])
def test_finish_reasons(finish, stop):
    assert from_openai_response(completion({"content": "hi"}, finish), "m").stop_reason == stop


# ------------------------------------------------------------------ requests, retries, errors


def test_request_shape_and_auth(router):
    route = router.post("/chat/completions").respond(200, json=completion({"content": "Hello"}))
    out = model()[0].complete("SYS", [{"role": "user", "content": "q"}], TOOL_DEFINITIONS)
    sent = json.loads(route.calls[0].request.content)
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {SECRET}"
    assert sent["model"] == "vendor/model:free" and sent["messages"][0] == {"role": "system", "content": "SYS"}
    assert sent["tools"][0]["type"] == "function" and sent["tools"][0]["function"]["parameters"]["type"] == "object"
    assert {"temperature", "tool_choice"}.isdisjoint(sent)
    assert out.content == [{"type": "text", "text": "Hello"}] and out.stop_reason == "end_turn"


def test_rate_limits_and_server_errors_are_retried(router):
    router.post("/chat/completions").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "5"}, json={"error": {"code": 429, "message": "slow down"}}),
        httpx.Response(502, text="bad gateway"),
        httpx.ConnectError("down"),
        httpx.Response(200, json=completion({"content": "ok"})),
    ])
    m, sleeps = model()
    assert m.complete("s", [], []).content[0]["text"] == "ok"
    assert len(sleeps) == 3 and sleeps[0] == 5.0


def test_an_error_hidden_inside_a_200_is_still_handled(router):
    router.post("/chat/completions").mock(side_effect=[
        httpx.Response(200, json={"error": {"code": 429, "message": "free tier busy"}}),
        httpx.Response(200, json=completion({"content": "ok"})),
    ])
    m, sleeps = model()
    assert m.complete("s", [], []).content[0]["text"] == "ok" and len(sleeps) == 1


def test_gives_up_after_the_retries_with_a_clear_message(router):
    router.post("/chat/completions").respond(503, text="x")
    m, sleeps = model(max_retries=2)
    with pytest.raises(ModelError, match=r"unavailable right now \(HTTP 503\) after 3 attempts"):
        m.complete("s", [], [])
    assert len(sleeps) == 2


@pytest.mark.parametrize(("status", "message"), [(401, "key was rejected"), (402, "no credit"), (404, "could not find"), (400, r"rejected \(400\)")])
def test_permanent_errors_are_not_retried_and_never_show_the_key(router, status, message):
    router.post("/chat/completions").respond(status, json={"error": {"message": "nope"}})
    m, sleeps = model()
    with pytest.raises(ModelError, match=message) as info:
        m.complete("s", [], [])
    assert sleeps == [] and SECRET not in str(info.value)


def test_missing_key_is_a_clear_error():
    with pytest.raises(ModelError, match="OPENROUTER_API_KEY is not set"):
        OpenRouterModel("m", None)


# ------------------------------------------------------------------ settings, cost, CLI


def test_free_models_cost_nothing():
    assert estimate_cost_usd("nvidia/nemotron-3-ultra-550b-a55b:free", Usage(1000, 1000)) == 0.0


def test_cli_picks_the_provider_and_the_dev_model():
    from pydantic import SecretStr

    s = get_settings().model_copy(update={
        "agent_provider": "openrouter", "openrouter_api_key": SecretStr(SECRET),
        "agent_model": "a/big:free", "agent_dev_model": "a/small:free"})
    assert isinstance(cli._real_model(s, dev=False), OpenRouterModel)
    assert cli._real_model(s, dev=False).name == "a/big:free" and cli._real_model(s, dev=True).name == "a/small:free"
    assert SECRET not in repr(s) and SECRET not in str(s)


def test_cli_without_an_openrouter_key_fails_cleanly(monkeypatch, capsys):
    s = get_settings().model_copy(update={"agent_provider": "openrouter", "openrouter_api_key": None})
    monkeypatch.setattr(cli, "get_settings", lambda: s)
    assert cli.run(argparse.Namespace(command="ask", question="q", verbose=False, dev=False)) == 2
    assert "OPENROUTER_API_KEY is not set" in capsys.readouterr().err
