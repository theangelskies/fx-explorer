"""Terminal commands with a FakeModel (no API cost) and respx (no real API)."""

import argparse
import json

import httpx
import pytest
import respx

from fx_explorer.agent import cli
from fx_explorer.agent.model import FakeModel, text_response, tool_response
from fx_explorer.config import get_settings

from .test_tools import ALERT, PAIR

API = "http://api.test"
CREATE = {"base": "EUR", "quote": "USD", "condition": "above", "threshold": "1.20"}
CURRENCIES = {"data": [{"iso_code": "USD", "name": "US Dollar", "type": "currency", "iso_numeric": None, "symbol": None,
                        "start_date": None, "end_date": None, "rates_count": 9, "latest_rate_date": "2026-11-26"}],
              "page": {"limit": 50, "next_cursor": None}}


@pytest.fixture
def api():
    with respx.mock(base_url=API, assert_all_called=False) as router:
        router.get("/v1/health").respond(200, json={"status": "ok", "database": "ok"})
        router.get("/v1/currencies").respond(200, json=CURRENCIES)
        yield router


@pytest.fixture
def settings():
    return get_settings().model_copy(update={"agent_api_base_url": API})


def run(command, model, settings, tmp_path, answers=(), question="q", verbose=False, interactive=True):
    args = argparse.Namespace(command=command, question=question, verbose=verbose, dev=False)
    typed = iter(answers)
    lines: list[str] = []
    code = cli.run(
        args, settings=settings, model=model, input_fn=lambda prompt: next(typed),
        out=lambda *a: lines.append(" ".join(map(str, a))), interactive=interactive, log_dir=tmp_path,
    )
    return code, "\n".join(lines)


def test_ask_verbose_prints_steps_cost_and_saves_a_transcript(api, settings, tmp_path):
    api.get("/v1/pairs/EUR/NGN").respond(200, json=PAIR)
    model = FakeModel([tool_response(("get_pair_trend", {"base": "EUR", "quote": "NGN", "window": "3y"}), text="Checking."),
                       text_response("It rose 67.76%.")])
    code, output = run("ask", model, settings, tmp_path, verbose=True)
    assert code == 0
    assert "── step 1" in output and "request: GET /v1/pairs/EUR/NGN?window=3y  -> 200" in output
    assert "It rose 67.76%." in output and "sources:" in output and "est. cost" in output
    [saved] = list(tmp_path.glob("*.json"))
    assert json.loads(saved.read_text())["result"]["sources"] == ["GET /v1/pairs/EUR/NGN?window=3y"]


def test_without_verbose_steps_are_hidden(api, settings, tmp_path):
    _, output = run("ask", FakeModel([text_response("Hello.")]), settings, tmp_path)
    assert "── step" not in output and "Hello." in output


def test_alert_confirmed_with_y_creates_it_once_with_the_idempotency_key(api, settings, tmp_path):
    post = api.post("/v1/alerts").respond(201, json=ALERT)
    model = FakeModel([tool_response(("create_alert", CREATE)), text_response("Please confirm: EUR/USD above 1.20.")])
    code, output = run("ask", model, settings, tmp_path, answers=["y"], question="Alert me above 1.20")
    assert post.call_count == 1 and "Alert created: EUR/USD above 1.2" in output
    assert "Pending alert" in output and "Confirm? [y/N]" not in output  # prompt text goes to input(), not out
    assert post.calls[0].request.headers["Idempotency-Key"]


@pytest.mark.parametrize("answer", ["n", "", "no", "maybe"])
def test_anything_but_yes_cancels_and_creates_nothing(api, settings, tmp_path, answer):
    post = api.post("/v1/alerts").respond(201, json=ALERT)
    model = FakeModel([tool_response(("create_alert", CREATE)), text_response("Please confirm.")])
    _, output = run("ask", model, settings, tmp_path, answers=[answer])
    assert post.call_count == 0 and "Cancelled. No alert was created." in output


def test_when_not_interactive_nothing_is_created_and_the_user_is_told(api, settings, tmp_path):
    post = api.post("/v1/alerts").respond(201, json=ALERT)
    model = FakeModel([tool_response(("create_alert", CREATE)), text_response("Please confirm.")])
    _, output = run("ask", model, settings, tmp_path, interactive=False)
    assert post.call_count == 0 and "Run `chat` to confirm" in output


def test_chat_keeps_the_conversation_and_tells_the_model_what_the_user_decided(api, settings, tmp_path):
    api.post("/v1/alerts").respond(201, json=ALERT)
    model = FakeModel([
        tool_response(("create_alert", CREATE)), text_response("Please confirm."),   # turn 1
        text_response("Anything else?"),                                             # turn 2
    ])
    code, output = run("chat", model, settings, tmp_path,
                       answers=["Alert me above 1.20", "y", "thanks", "exit"])
    assert code == 0
    second_turn = model.calls[2]["messages"]
    assert second_turn[0]["content"] == "Alert me above 1.20"            # history kept
    last = second_turn[-1]["content"]
    assert last.startswith("[App notice: the user confirmed and the alert was created: EUR/USD above 1.2") and last.endswith("thanks")


def test_chat_cancel_is_reported_to_the_model_too(api, settings, tmp_path):
    post = api.post("/v1/alerts").respond(201, json=ALERT)
    model = FakeModel([tool_response(("create_alert", CREATE)), text_response("Confirm?"), text_response("OK.")])
    run("chat", model, settings, tmp_path, answers=["alert please", "n", "ok", "quit"])
    assert post.call_count == 0
    assert "the user cancelled the proposed alert" in model.calls[2]["messages"][-1]["content"]


def test_a_failed_confirm_is_shown_and_the_user_can_see_why(api, settings, tmp_path):
    api.post("/v1/alerts").respond(503, json={"title": "x", "status": 503, "detail": "db down"})
    model = FakeModel([tool_response(("create_alert", CREATE)), text_response("Confirm?")])
    _, output = run("ask", model, settings, tmp_path, answers=["y"])
    assert "Could not create the alert: Error 503" in output


def test_api_down_gives_a_helpful_message_and_exit_code_2(settings, tmp_path, capsys):
    with respx.mock(base_url=API) as router:
        router.get("/v1/health").mock(side_effect=httpx.ConnectError("refused"))
        code, _ = run("ask", FakeModel([]), settings, tmp_path)
    assert code == 2 and "can't reach the API" in capsys.readouterr().err


def test_missing_key_message_never_shows_a_key(monkeypatch, settings, tmp_path, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: settings.model_copy(update={"agent_provider": "anthropic", "anthropic_api_key": None}))
    args = argparse.Namespace(command="ask", question="q", verbose=False, dev=False)
    assert cli.run(args) == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_dev_flag_picks_the_cheaper_model(settings):
    from pydantic import SecretStr

    s = settings.model_copy(update={"agent_provider": "anthropic", "anthropic_api_key": SecretStr("sk-test-not-real")})
    assert cli._real_model(s, dev=True).name == s.agent_dev_model
    assert cli._real_model(s, dev=False).name == s.agent_model
    assert "sk-test-not-real" not in repr(s) and "sk-test-not-real" not in str(s)
