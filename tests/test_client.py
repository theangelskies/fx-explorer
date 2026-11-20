"""Client tests. respx fakes the network, so nothing here calls the real API."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from fx_explorer.frankfurter.client import (
    ApiError,
    FrankfurterClient,
    RetriesExhaustedError,
)

BASE = "https://api.test/v2"
RATES_OK = [{"date": "2026-11-20", "base": "EUR", "quote": "NGN", "rate": 1512.17}]


@pytest.fixture
def sleeps() -> list[float]:
    """Collects every pause the client asks for, instead of really sleeping."""
    return []


@pytest.fixture
def client(sleeps):
    c = FrankfurterClient(
        BASE, timeout_seconds=1, max_retries=3, request_delay_seconds=0, sleep=sleeps.append
    )
    yield c
    c.close()


@pytest.fixture
def api():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        yield mock


def test_success_returns_decimals_and_sends_params(client, api):
    route = api.get("/rates").mock(return_value=httpx.Response(200, json=RATES_OK))

    rows = client.get_rates("EUR", ["USD", "NGN"], date=date(2026, 11, 20))

    assert rows[0]["rate"] == Decimal("1512.17")
    assert isinstance(rows[0]["rate"], Decimal)  # not a float
    request = route.calls.last.request
    assert request.url.path == "/v2/rates"
    assert dict(request.url.params) == {"base": "EUR", "quotes": "USD,NGN", "date": "2026-11-20"}


def test_get_currencies(client, api):
    api.get("/currencies").mock(
        return_value=httpx.Response(200, json=[{"iso_code": "USD", "name": "US Dollar", "type": "currency"}])
    )
    assert client.get_currencies()[0]["iso_code"] == "USD"


def test_range_uses_from_and_to(client, api):
    route = api.get("/rates").mock(return_value=httpx.Response(200, json=[]))
    client.get_rates("EUR", ["USD"], start=date(2026, 11, 1), end=date(2026, 11, 20))
    assert dict(route.calls.last.request.url.params)["from"] == "2026-11-01"
    assert dict(route.calls.last.request.url.params)["to"] == "2026-11-20"


def test_date_and_range_are_mutually_exclusive(client):
    with pytest.raises(ValueError):
        client.get_rates("EUR", ["USD"], date=date(2026, 11, 20), start=date(2026, 11, 1))


def test_503_then_success(client, api, sleeps):
    route = api.get("/rates").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=RATES_OK)]
    )
    assert client.get_rates("EUR", ["NGN"]) == [
        {"date": "2026-11-20", "base": "EUR", "quote": "NGN", "rate": Decimal("1512.17")}
    ]
    assert route.call_count == 2
    assert len(sleeps) == 1 and 1.0 <= sleeps[0] <= 1.25  # first backoff is ~1s


def test_backoff_grows_1_2_4(client, api, sleeps):
    api.get("/rates").mock(
        side_effect=[httpx.Response(500)] * 3 + [httpx.Response(200, json=RATES_OK)]
    )
    client.get_rates("EUR", ["NGN"])
    assert [1.0 <= sleeps[0] <= 1.25, 2.0 <= sleeps[1] <= 2.5, 4.0 <= sleeps[2] <= 5.0] == [True] * 3


def test_retry_after_header_is_honoured(client, api, sleeps):
    api.get("/rates").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=RATES_OK),
        ]
    )
    client.get_rates("EUR", ["NGN"])
    assert sleeps == [7.0]  # exactly what the server asked for, not our own backoff


def test_timeout_then_success(client, api, sleeps):
    route = api.get("/rates").mock(
        side_effect=[httpx.ConnectTimeout("too slow"), httpx.Response(200, json=RATES_OK)]
    )
    assert len(client.get_rates("EUR", ["NGN"])) == 1
    assert route.call_count == 2
    assert len(sleeps) == 1


def test_network_error_is_retried(client, api):
    route = api.get("/rates").mock(
        side_effect=[httpx.ConnectError("no route"), httpx.Response(200, json=RATES_OK)]
    )
    client.get_rates("EUR", ["NGN"])
    assert route.call_count == 2


@pytest.mark.parametrize("status", [400, 404, 422])
def test_client_errors_are_not_retried(client, api, sleeps, status):
    route = api.get("/rates").mock(
        return_value=httpx.Response(status, json={"status": status, "message": "invalid currency: XXX"})
    )
    with pytest.raises(ApiError) as excinfo:
        client.get_rates("XXX", ["USD"])
    assert route.call_count == 1  # tried once only
    assert sleeps == []
    assert excinfo.value.status_code == status
    assert excinfo.value.message == "invalid currency: XXX"


def test_retries_exhausted_raises(client, api, sleeps):
    route = api.get("/rates").mock(return_value=httpx.Response(503))
    with pytest.raises(RetriesExhaustedError, match="4 attempts"):
        client.get_rates("EUR", ["NGN"])
    assert route.call_count == 4  # 1 first try + 3 retries
    assert len(sleeps) == 3  # no pointless sleep after the last failure


def test_throttle_waits_between_requests(api, sleeps):
    api.get("/currencies").mock(return_value=httpx.Response(200, json=[]))
    with FrankfurterClient(BASE, request_delay_seconds=1, sleep=sleeps.append) as c:
        c.get_currencies()
        assert sleeps == []  # the first request goes out immediately
        c.get_currencies()
    assert len(sleeps) == 1 and 0.5 < sleeps[0] <= 1.0
