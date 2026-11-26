"""GET /v1/rates: filters, sorting, cursor pagination, 400s."""

import pytest

from tests.api.conftest import EUR_USD

pytestmark = pytest.mark.integration


def get_all(client, query: str) -> tuple[list[dict], int]:
    """Follow next_cursor from the first page until it is null, like a real client."""
    rows, pages, cursor = [], 0, None
    while True:
        url = f"/v1/rates?{query}" + (f"&cursor={cursor}" if cursor else "")
        resp = client.get(url)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        pages += 1
        rows += body["data"]
        cursor = body["page"]["next_cursor"]
        if cursor is None:
            return rows, pages


def test_first_page_shape(seeded):
    body = seeded.get("/v1/rates?quotes=USD&limit=2").json()
    assert body["data"] == [
        {"rate_date": "2026-11-17", "base": "EUR", "quote": "USD", "rate": "1.1005",
         "updated_at": body["data"][0]["updated_at"]},
        {"rate_date": "2026-11-16", "base": "EUR", "quote": "USD", "rate": "1.1034",
         "updated_at": body["data"][1]["updated_at"]},
    ]
    assert body["data"][0]["updated_at"].endswith("Z")
    assert body["page"]["limit"] == 2 and isinstance(body["page"]["next_cursor"], str)


def test_decimals_are_strings_without_exponent(seeded):
    rate = seeded.get("/v1/rates?quotes=XAU").json()["data"][0]["rate"]
    assert rate == "0.00029946"


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 11, 200])
def test_pages_have_no_duplicates_or_gaps(seeded, limit):
    rows, pages = get_all(seeded, f"limit={limit}")
    keys = [(r["rate_date"], r["quote"]) for r in rows]
    assert len(keys) == 11 and len(set(keys)) == 11
    assert pages == -(-11 // limit)  # never an extra empty page
    # default order: newest date first, then quote A->Z
    assert keys == sorted(keys, key=lambda k: (-int(k[0].replace("-", "")), k[1]))


def test_sort_oldest_first(seeded):
    rows, _ = get_all(seeded, "sort=date_asc&limit=3")
    keys = [(r["rate_date"], r["quote"]) for r in rows]
    assert keys == sorted(keys) and len(keys) == 11


def test_filters_quotes_and_dates_inclusive(seeded):
    rows, _ = get_all(seeded, "quotes=USD,GBP&from=2026-11-15&to=2026-11-16")
    assert [(r["rate_date"], r["quote"]) for r in rows] == [
        ("2026-11-16", "GBP"), ("2026-11-16", "USD"),
        ("2026-11-15", "GBP"), ("2026-11-15", "USD"),
    ]


def test_values_match_samples(seeded):
    rows, _ = get_all(seeded, "quotes=USD&sort=date_asc")
    assert {r["rate_date"]: r["rate"] for r in rows} == EUR_USD


def test_future_range_is_empty_200(seeded):
    resp = seeded.get("/v1/rates?from=2030-01-01")
    assert resp.status_code == 200
    assert resp.json() == {"data": [], "page": {"limit": 50, "next_cursor": None}}


def test_known_base_without_rates_is_empty_200(seeded):
    assert seeded.get("/v1/rates?base=BTC").json()["data"] == []


@pytest.mark.parametrize(
    ("query", "field", "message"),
    [
        ("base=XYZ", "base", "Unknown currency: XYZ."),
        ("base=eur", "base", "Must be a currency code of 3–4 uppercase letters, e.g. USD."),
        ("quotes=USD,XYZ,ABC", "quotes", "Unknown currencies: XYZ, ABC."),
        ("quotes=USD,usd", "quotes", None),
        ("quotes=", "quotes", None),
        ("from=2026-13-01", "from", "Must be a valid date in YYYY-MM-DD format."),
        ("from=2026-02-30", "from", "Must be a valid date in YYYY-MM-DD format."),
        ("to=20261113", "to", "Must be a valid date in YYYY-MM-DD format."),
        ("from=2026-11-17&to=2026-11-13", "from", "Must be on or before `to` (2026-11-13)."),
        ("sort=newest", "sort", None),
        ("limit=0", "limit", "Must be at least 1."),
        ("limit=201", "limit", "Must be at most 200."),
        ("limit=ten", "limit", "Must be a whole number."),
        ("cursor=%%%", "cursor", None),
        ("cursor=eyJkIjoibm90LWEtZGF0ZSIsInEiOiJVU0QifQ", "cursor", None),  # {"d":"not-a-date",...}
    ],
)
def test_bad_params_400(seeded, query, field, message):
    resp = seeded.get(f"/v1/rates?{query}")
    assert resp.status_code == 400, resp.text
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["type"] == "/problems/invalid-parameters"
    assert [e["field"] for e in body["errors"]] == [field]
    if message:
        assert body["errors"][0]["message"] == message


def test_several_bad_params_reported_together(seeded):
    body = seeded.get("/v1/rates?quotes=usd&from=2026-13-01&to=2026-14-01").json()
    assert {e["field"] for e in body["errors"]} == {"quotes", "from", "to"}
    assert body["detail"] == "3 parameters are invalid."


def test_unknown_currency_and_bad_dates_reported_together(seeded):
    body = seeded.get("/v1/rates?base=XYZ&from=2026-11-20&to=2026-11-01").json()
    assert {e["field"] for e in body["errors"]} == {"base", "from"}
