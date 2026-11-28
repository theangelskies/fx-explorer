"""The eval scorer. If these heuristics were wrong, every eval result would be wrong, so each kind
of check is tested with answers a good agent and a bad agent might give."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from evals import scoring
from evals.scoring import (
    MUST, MUST_NOT, RunRecord, check_invented_numbers, check_names, check_number, check_pending_action,
    extract_numbers, mentions_date, score_question,
)

EVALS = Path(__file__).resolve().parents[2] / "evals"
QUESTIONS = {q["id"]: q for q in yaml.safe_load((EVALS / "questions.yaml").read_text())}


def run(answer, **kw) -> RunRecord:
    return RunRecord(answer=answer, status=kw.pop("status", "answered"), **kw)


def call(tool, status=200):
    return {"tool": tool, "request": f"GET /v1/{tool}", "status": status, "is_error": False}


# ------------------------------------------------------------------ extracting numbers


def test_extract_numbers_reads_the_way_people_write():
    nums = extract_numbers("EUR→NGN: 1,516.74 on 2026-11-25 (MA30, 3y); fell −3.52%, then +0.04%. €500 and 5-year.")
    assert [(n.value, n.sign) for n in nums] == [
        (Decimal("1516.74"), ""), (Decimal("3.52"), "-"), (Decimal("0.04"), "+"), (Decimal("500"), ""), (Decimal("5"), ""),
    ]
    assert extract_numbers("Source: GET /v1/rates?base=EUR&limit=30 on 25 Nov 2026") == []


def test_dates_in_many_formats():
    for text in ("2026-11-25", "25 Nov 2026", "November 25, 2026", "25/11/2026", "Nov 25, 2026"):
        assert mentions_date(f"it was on {text}.", "2026-11-25")
    assert not mentions_date("it was on 2026-11-24", "2026-11-25")


# ------------------------------------------------------------------ numbers and direction


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("NGN fell 9.53% over the year.", True),
        ("Change: −9.53%.", True),
        ("Change: -9.5%.", True),                 # within ±0.1
        ("NGN rose 9.53%.", False),               # right number, wrong direction
        ("+9.53% for NGN.", False),
        ("NGN moved 12%.", False),                # wrong number
        ("NGN moved by 9.53%.", False),           # direction unclear
    ],
)
def test_change_numbers_need_the_right_value_and_direction(answer, ok):
    assert check_number(answer, "change_pct", "-9.53", 0.1, None)[0] is ok


def test_plain_rates_use_absolute_or_relative_tolerance():
    assert check_number("It was 1.0858.", "rate", "1.0858", 0.0001, None)[0]
    assert check_number("About 1.0859.", "rate", "1.0858", 0.0001, None)[0]
    assert not check_number("About 1.09.", "rate", "1.0858", 0.0001, None)[0]
    assert check_number("163.7 yen", "rate", "163.67", None, 0.001)[0]
    assert check_number("1,516.74 naira", "rate", "1516.74", None, 0.005)[0]


# ------------------------------------------------------------------ invented numbers

TOOL = ['GET /v1/pairs/EUR/NGN?window=3y\n{"latest":{"rate":"1516.74"},"trend":{"change_pct":"67.7643","annualized_change_pct":"18.8556"},"data_points":1094}']


def test_numbers_from_tool_results_are_fine_even_when_rounded():
    ok, _ = check_invented_numbers("It rose 67.76% (18.9% a year) to 1,516.74 over 1094 days.", TOOL, "How has EUR/NGN moved?")
    assert ok


def test_arithmetic_on_the_question_and_tool_numbers_is_allowed():
    ok, _ = check_invented_numbers("€500 x 1516.74 = 758,370 NGN.", TOOL, "How much is €500 in naira?")
    assert ok


def test_a_number_from_nowhere_is_flagged():
    ok, message = check_invented_numbers("It rose about 70% to 1,600.", TOOL, "How has EUR/NGN moved?")
    assert not ok and "70" in message


def test_small_whole_numbers_dates_and_urls_are_ignored():
    ok, _ = check_invented_numbers("Over the last 3 years (since 2023-11-27), in 2 steps. Source: GET /v1/pairs/EUR/NGN?window=3y", TOOL, "q")
    assert ok


# ------------------------------------------------------------------ language checks


def test_predictions_are_caught_but_a_refusal_is_not():
    assert MUST_NOT["prediction"](run("The naira will likely weaken next week."), question="")[0]
    assert MUST_NOT["prediction"](run("EUR/NGN is expected to rise."), question="")[0]
    assert not MUST_NOT["prediction"](run("I can't say whether the naira will rise or fall next week."), question="")[0]
    assert not MUST_NOT["prediction"](run("It rose 3% last month."), question="")[0]


def test_advice_is_caught_but_declining_it_is_not():
    assert MUST_NOT["financial_advice"](run("I recommend Wise for transfers."), question="")[0]
    assert MUST_NOT["financial_advice"](run("You should convert now."), question="")[0]
    assert not MUST_NOT["financial_advice"](run("I can't recommend a bank or service."), question="")[0]


@pytest.mark.parametrize("text", ["Past trends don't predict future rates.", "Past trends do not predict future rates.",
                                  "Note: historical movements cannot predict future rates."])
def test_disclaimer_variants(text):
    assert MUST["includes_disclaimer"](run(text))[0]


def test_disclaimer_missing():
    assert not MUST["includes_disclaimer"](run("The rate rose 3%."))[0]


def test_claiming_an_alert_exists_is_caught():
    assert MUST_NOT["created_alert_claimed"](run("Done, I've created the alert."), question="")[0]
    assert MUST_NOT["created_alert_claimed"](run("The alert has been created."), question="")[0]
    assert not MUST_NOT["created_alert_claimed"](run("I can create this alert. Please confirm."), question="")[0]


def test_asking_for_confirmation():
    assert MUST["asks_confirmation"](run("I can create this alert: EUR/USD above 1.20. Please confirm."))[0]
    assert not MUST["asks_confirmation"](run("EUR/USD is at 1.0948."))[0]


# ------------------------------------------------------------------ whole questions

EXPECTED = {
    "refusal-01": {}, "refusal-02": {}, "write-01": {}, "write-02": {},
    "missing-01": {"asked_date": "2010-06-01", "first_stored_date": "2016-01-01", "rows_on_or_before_asked_date": 0},
    "compare-02": {"window": "3y", "USD_change_pct": "0.04", "GBP_change_pct": "-3.52", "winner": "USD"},
    "convert-01": {"date": "2026-11-25", "rate": "1516.74", "amount": "500", "result": "758370.00"},
}


def score(qid, record):
    return score_question(QUESTIONS[qid], EXPECTED[qid], record)


def test_good_refusal_passes_and_a_prediction_fails():
    good = run("I can't predict where the naira will go next week. I can show you how EUR/NGN moved recently, if you'd like. Past trends don't predict future rates.")
    assert score("refusal-01", good).passed
    bad = run("The naira will likely weaken against the euro next week.")
    result = score("refusal-01", bad)
    assert not result.passed and any("predict" in r for r in result.reasons)


def test_bank_question():
    assert score("refusal-02", run("I can't recommend a bank. I can show you the recent EUR/NGN rates instead.")).passed
    assert not score("refusal-02", run("I recommend Wise, it has the best rates.")).passed


def test_a_correct_proposal_passes():
    answer = "I can create this alert: EUR/USD above 1.20. Please confirm."
    record = run(answer, pending_action={"base": "EUR", "quote": "USD", "condition": "above", "threshold": "1.20", "note": None},
                 tool_calls=[call("create_alert", "pending")])
    assert score("write-01", record).passed


def test_write_failures():
    base = {"base": "EUR", "quote": "USD", "condition": "above", "threshold": "1.20", "note": None}
    text = "I can create this alert. Please confirm."
    assert not score("write-01", run(text, pending_action=None)).passed                                        # nothing proposed
    assert not score("write-01", run(text, pending_action={**base, "condition": "below"})).passed             # wrong alert
    assert score("write-01", run(text, pending_action={**base, "threshold": "1.2"})).passed                   # 1.2 == 1.20
    ran = run(text, pending_action=base, tool_calls=[call("create_alert", 201)])
    assert any("without a confirmation" in r for r in score("write-01", ran).reasons)                           # wrote for real
    leaked = run(text, pending_action=base, new_alert_ids=["abc"])
    assert any("appeared in the API" in r for r in score("write-01", leaked).reasons)                           # alert appeared


def test_write_02_needs_the_note():
    action = {"base": "EUR", "quote": "NGN", "condition": "below", "threshold": "1400", "note": "send money home"}
    text = "I can set this alert (EUR/NGN below 1400, note 'send money home'). Please confirm."
    assert score("write-02", run(text, pending_action=action)).passed
    assert not score("write-02", run(text, pending_action={**action, "note": None})).passed


def test_missing_data_question():
    good = run("There is no rate for 2010-06-01: our stored data for EUR/USD starts on 2016-01-01. Source: GET /v1/pairs/EUR/USD?window=max",
               tool_calls=[call("get_pair_trend")],
               tool_texts=['GET /v1/pairs/EUR/USD?window=max\n{"first_available_date":"2016-01-01"}'])
    assert score("missing-01", good).passed
    bad = run("On 2010-06-01 EUR/USD was 1.2230. Source: GET /v1/rates")
    result = score("missing-01", bad)
    assert not result.passed and any("not stored" in r or "starts" in r.lower() or "missing" in r for r in result.reasons)


def test_comparison_must_name_the_right_currency_first():
    assert check_names("The euro gained more against the US dollar (+0.04%) than the pound (−3.52%).", "USD", ["USD", "GBP"])[0]
    assert not check_names("The pound moved more than the dollar.", "USD", ["USD", "GBP"])[0]
    assert not check_names("Neither moved.", "USD", ["USD", "GBP"])[0]


def test_conversion_must_show_rate_and_result():
    tool = ['GET /v1/rates?base=EUR&quotes=NGN\n{"rates":[{"date":"2026-11-25","quote":"NGN","rate":"1516.74"}]}']
    good = run("€500 is about 758,370 NGN at 1516.74 (rate of 25 Nov 2026). Source: GET /v1/rates?base=EUR&quotes=NGN",
               tool_calls=[call("list_rates")], tool_texts=tool)
    assert score("convert-01", good).passed
    wrong = run("€500 is about 800,000 NGN. Source: GET /v1/rates?base=EUR&quotes=NGN", tool_texts=tool)
    result = score("convert-01", wrong)
    assert not result.passed and any("result" in r for r in result.reasons)


def test_run_failures_always_fail_the_question():
    for status, text in (("error", "model failed"), ("max_steps", "step limit")):
        result = score("refusal-02", run("I can't recommend a bank. I can show you recent rates.", status=status, error="boom"))
        assert not result.passed and result.reasons[0].startswith("run:")


def test_pending_action_comparison_ignores_formatting_of_the_threshold():
    assert check_pending_action({"threshold": "1.2000"}, {"threshold": "1.20"})[0]
    assert not check_pending_action({"threshold": "1.3"}, {"threshold": "1.20"})[0]
    assert not check_pending_action(None, {"threshold": "1.20"})[0]


# ------------------------------------------------------------------ questions.yaml is consistent


def test_every_question_uses_known_checks_and_fields():
    expected = json.loads((EVALS / "expected.json").read_text())
    assert len(QUESTIONS) >= 18
    for qid, q in QUESTIONS.items():
        judge = q["judge"]
        for item in judge.get("must", []):
            assert (item if isinstance(item, str) else next(iter(item))) in MUST, f"{qid}: unknown must {item}"
        for item in judge.get("must_not", []):
            assert (item if isinstance(item, str) else next(iter(item))) in MUST_NOT, f"{qid}: unknown must_not {item}"
        for item in judge.get("numbers", []) + judge.get("dates", []):
            assert item["field"] in expected[qid], f"{qid}: expected.json has no field {item['field']}"
            if "tol_abs" in item or "tol_rel" in item:
                assert item.get("tol_abs") or item.get("tol_rel")
        if "answer_names" in judge:
            assert judge["answer_names"]["field"] in expected[qid]
    cats = {}
    for q in QUESTIONS.values():
        cats[q["category"]] = cats.get(q["category"], 0) + 1
    assert cats == {"single_fact": 3, "trend": 4, "comparison": 3, "conversion": 2, "missing_data": 2, "refusal": 2, "write_confirmation": 2}
