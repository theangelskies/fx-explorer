"""POST/GET/DELETE /v1/alerts. Seed data: EUR/USD latest rate is 1.1005 (2026-11-17)."""

import uuid

import pytest

pytestmark = pytest.mark.integration

VALID = {"base": "EUR", "quote": "USD", "condition": "above", "threshold": "1.1200",
         "note": "Buy dollars for the trip"}


def create(client, **changes):
    return client.post("/v1/alerts", json={**VALID, **changes})


def errors_of(resp) -> dict[str, str]:
    assert resp.status_code == 422, resp.text
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["type"] == "/problems/validation-error"
    return {e["field"]: e["message"] for e in body["errors"]}


# ------------------------------------------------------------------- create

def test_create_returns_201_location_and_alert(seeded):
    resp = create(seeded)
    assert resp.status_code == 201
    alert = resp.json()
    assert resp.headers["location"] == f"/v1/alerts/{alert['id']}"
    uuid.UUID(alert["id"])
    assert alert == {
        "id": alert["id"], "base": "EUR", "quote": "USD", "condition": "above",
        "threshold": "1.1200",  # exactly as sent: trailing zeros kept
        "note": "Buy dollars for the trip", "created_at": alert["created_at"],
        "current_rate": "1.1005",  # the latest stored EUR/USD rate
        "triggered": False,  # 1.1005 is not >= 1.1200
    }
    assert alert["created_at"].endswith("Z")
    assert seeded.get(resp.headers["location"]).json() == alert


def test_note_optional_and_nullable(seeded):
    no_note = {k: v for k, v in VALID.items() if k != "note"}
    assert seeded.post("/v1/alerts", json=no_note).json()["note"] is None
    assert create(seeded, note=None).json()["note"] is None


@pytest.mark.parametrize(("condition", "threshold", "triggered"),
                         [("above", "1.1", True), ("below", "1.1005", True), ("below", "1.1", False)])
def test_triggered_is_computed_from_latest_rate(seeded, condition, threshold, triggered):
    assert create(seeded, condition=condition, threshold=threshold).json()["triggered"] is triggered


def test_tiny_threshold_with_10_decimals(seeded):
    assert create(seeded, quote="XAU", threshold="0.0002994612").json()["threshold"] == "0.0002994612"


# --------------------------------------------------------------- validation

THRESHOLD_MSG = 'Must be a number greater than 0 with at most 10 decimal places, e.g. "1.2500".'
CODE_MSG = "Must be a currency code of 3–4 uppercase letters, e.g. USD."


@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        ({"threshold": "0"}, "threshold", THRESHOLD_MSG),
        ({"threshold": "0.0000"}, "threshold", THRESHOLD_MSG),
        ({"threshold": "-1"}, "threshold", THRESHOLD_MSG),
        ({"threshold": "1.12345678901"}, "threshold", THRESHOLD_MSG),  # 11 decimals
        ({"threshold": "1e3"}, "threshold", THRESHOLD_MSG),
        ({"threshold": "1,5"}, "threshold", THRESHOLD_MSG),
        ({"threshold": " 1"}, "threshold", THRESHOLD_MSG),
        ({"threshold": 1.12}, "threshold", THRESHOLD_MSG),  # a JSON number, not a string
        ({"condition": "sideways"}, "condition", "Must be one of: above or below."),
        ({"note": "x" * 201}, "note", "Must be at most 200 characters."),
        ({"note": 5}, "note", "Must be a string."),
        ({"base": "eur"}, "base", CODE_MSG),
        ({"quote": "DOLLARS"}, "quote", CODE_MSG),
        ({"base": "XYZ"}, "base", "Unknown currency: XYZ."),
        ({"quote": "ABCD"}, "quote", "Unknown currency: ABCD."),
        ({"quote": "EUR"}, "quote", "Must be different from base."),
        ({"quote": "BTC"}, "quote", "No rates are stored for EUR/BTC, so this alert could never trigger."),
        ({"treshold": "1.2"}, "treshold", "Unknown field."),
    ],
)
def test_each_rule_gives_a_readable_field_error(seeded, changes, field, message):
    errors = errors_of(create(seeded, **changes))
    assert errors[field] == message


@pytest.mark.parametrize("missing", ["base", "quote", "condition", "threshold"])
def test_required_fields(seeded, missing):
    body = {k: v for k, v in VALID.items() if k != missing}
    assert errors_of(seeded.post("/v1/alerts", json=body)) == {missing: "This field is required."}


def test_all_problems_reported_at_once_one_per_field(seeded):
    resp = seeded.post("/v1/alerts", json={"base": "XYZ", "quote": "usd", "condition": "up",
                                           "threshold": "-5", "note": "x" * 300})
    errors = errors_of(resp)
    assert list(errors) == ["base", "quote", "condition", "threshold", "note"]  # form order
    assert errors["base"] == "Unknown currency: XYZ."  # a DB check, despite the shape errors
    assert resp.json()["detail"] == "5 fields are invalid."


@pytest.mark.parametrize(
    ("content", "message"),
    [(b"{not json", "Request body is not valid JSON."),
     (b"[1, 2]", "Request body must be a JSON object."),
     (b'"text"', "Request body must be a JSON object.")],
)
def test_body_that_is_not_an_object(seeded, content, message):
    resp = seeded.post("/v1/alerts", content=content, headers={"content-type": "application/json"})
    assert errors_of(resp) == {"body": message}


def test_missing_body(seeded):
    assert errors_of(seeded.post("/v1/alerts")) == {"body": "Request body is required."}


def test_idempotency_key_too_long_is_400(seeded):
    resp = seeded.post("/v1/alerts", json=VALID, headers={"Idempotency-Key": "k" * 256})
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["field"] == "Idempotency-Key"


# ------------------------------------------------------------ list/get/delete

def test_list_newest_first_with_status_filter_and_paging(seeded):
    ids = [create(seeded, threshold=t).json()["id"] for t in ("1.0", "1.2", "1.05", "1.3", "1.1")]
    seen, url = [], "/v1/alerts?limit=2"
    while url:
        body = seeded.get(url).json()
        seen += [a["id"] for a in body["data"]]
        cursor = body["page"]["next_cursor"]
        url = f"/v1/alerts?limit=2&cursor={cursor}" if cursor else None
    assert seen == ids[::-1]  # newest first, each exactly once

    triggered = seeded.get("/v1/alerts?status=triggered").json()["data"]
    waiting = seeded.get("/v1/alerts?status=waiting").json()["data"]
    # rate 1.1005 has reached 1.0, 1.05 and 1.1 (condition "above")
    assert sorted(a["threshold"] for a in triggered) == ["1.0", "1.05", "1.1"]
    assert sorted(a["threshold"] for a in waiting) == ["1.2", "1.3"]


def test_empty_list_is_200(seeded):
    assert seeded.get("/v1/alerts").json() == {"data": [], "page": {"limit": 50, "next_cursor": None}}


@pytest.mark.parametrize(("query", "field"),
                         [("status=done", "status"), ("limit=0", "limit"), ("cursor=abc", "cursor")])
def test_list_bad_params_400(seeded, query, field):
    resp = seeded.get(f"/v1/alerts?{query}")
    assert resp.status_code == 400
    assert resp.json()["errors"][0]["field"] == field


def test_delete_then_404(seeded):
    alert_id = create(seeded).json()["id"]
    resp = seeded.delete(f"/v1/alerts/{alert_id}")
    assert resp.status_code == 204 and resp.content == b""
    assert seeded.get(f"/v1/alerts/{alert_id}").status_code == 404
    again = seeded.delete(f"/v1/alerts/{alert_id}")
    assert again.status_code == 404
    assert again.json()["detail"] == f"No alert with id '{alert_id}'."


def test_unknown_id_404_and_malformed_id_400(seeded):
    assert seeded.get(f"/v1/alerts/{uuid.uuid4()}").status_code == 404
    resp = seeded.get("/v1/alerts/not-a-uuid")
    assert resp.status_code == 400
    assert resp.json()["errors"] == [{"field": "id", "message": "Must be a valid UUID."}]


@pytest.mark.parametrize(("method", "path", "allow"),
                         [("OPTIONS", "/v1/alerts", "GET, POST"),
                          ("PUT", "/v1/alerts/3f2b8c1e-6a4d-4f7e-9b1a-2c5d8e9f0a1b", "DELETE, GET")])
def test_405_allow_header_lists_every_method(seeded, method, path, allow):
    resp = seeded.request(method, path)
    assert resp.status_code == 405
    assert resp.headers["allow"] == allow
