"""GET /v1/currencies and GET /v1/currencies/{code}."""

import pytest

pytestmark = pytest.mark.integration

ALL = ["BTC", "EUR", "GBP", "USD", "USDT", "XAU"]


def codes(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [c["iso_code"] for c in resp.json()["data"]]


def test_list_shape_and_nulls(seeded):
    resp = seeded.get("/v1/currencies")
    body = resp.json()
    assert body["page"] == {"limit": 50, "next_cursor": None}
    btc = next(c for c in body["data"] if c["iso_code"] == "BTC")
    # every field present; missing values are null, never absent
    assert btc == {"iso_code": "BTC", "name": "Bitcoin", "type": "crypto", "iso_numeric": None,
                   "symbol": "₿", "start_date": None, "end_date": None, "rates_count": 0,
                   "latest_rate_date": None}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", ALL),
        ("type=crypto", ["BTC", "USDT"]),
        ("type=metal", ["XAU"]),
        ("q=dollar", ["USD"]),
        ("q=usd", ["USD", "USDT"]),
        ("has_rates=true", ["EUR", "GBP", "USD", "XAU"]),
        ("has_rates=false", ["BTC", "USDT"]),
        ("type=crypto&has_rates=true", []),
    ],
)
def test_filters(seeded, query, expected):
    assert codes(seeded.get(f"/v1/currencies?{query}")) == expected


def test_empty_result_is_200_with_empty_list(seeded):
    resp = seeded.get("/v1/currencies?q=zzzz")
    assert resp.status_code == 200
    assert resp.json() == {"data": [], "page": {"limit": 50, "next_cursor": None}}


def test_follow_cursor_until_null(seeded):
    seen, url, pages = [], "/v1/currencies?limit=4", 0
    while True:
        body = seeded.get(url).json()
        pages += 1
        seen += [c["iso_code"] for c in body["data"]]
        if body["page"]["next_cursor"] is None:
            break
        url = f"/v1/currencies?limit=4&cursor={body['page']['next_cursor']}"
    assert seen == ALL and pages == 2


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("type=fiat", "type"),
        ("has_rates=yes", "has_rates"),
        ("has_rates=1", "has_rates"),
        ("limit=0", "limit"),
        ("limit=201", "limit"),
        ("q=", "q"),
        ("cursor=not-a-cursor", "cursor"),
        ("cursor=eyJ4IjoiMSJ9", "cursor"),  # valid base64 JSON {"x":"1"}, wrong keys
    ],
)
def test_bad_params_400(seeded, query, field):
    resp = seeded.get(f"/v1/currencies?{query}")
    assert resp.status_code == 400
    assert resp.headers["content-type"] == "application/problem+json"
    assert [e["field"] for e in resp.json()["errors"]] == [field]


def test_get_one(seeded):
    resp = seeded.get("/v1/currencies/USD")
    assert resp.status_code == 200
    assert resp.json() == {
        "iso_code": "USD", "name": "United States Dollar", "type": "currency",
        "iso_numeric": "840", "symbol": "$", "start_date": "1948-06-21",
        "end_date": "2026-11-20", "rates_count": 5, "latest_rate_date": "2026-11-17",
    }


def test_get_unknown_is_404(seeded):
    resp = seeded.get("/v1/currencies/XYZ")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No currency with code 'XYZ'."


@pytest.mark.parametrize("code", ["usd", "US", "TOOLONG", "U1D"])
def test_get_malformed_code_is_400(seeded, code):
    resp = seeded.get(f"/v1/currencies/{code}")
    assert resp.status_code == 400
    assert resp.json()["errors"] == [
        {"field": "code", "message": "Must be a currency code of 3–4 uppercase letters, e.g. USD."}]
