"""Idempotency-Key on POST /v1/alerts (ADR 0006)."""

import threading
import time
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fx_explorer.api.idempotency import request_hash
from fx_explorer.domain.models import ExchangeRate
from fx_explorer.repositories.alerts import AlertRepository
from fx_explorer.repositories.idempotency import IdempotencyRepository
from fx_explorer.repositories.rates import RateRepository

pytestmark = pytest.mark.integration

BODY = {"base": "EUR", "quote": "USD", "condition": "above", "threshold": "1.1200"}
KEY = "8e03978e-40d5-43e8-bc93-6894a57f9324"


def post(client, body=BODY, key=KEY):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/v1/alerts", json=body, headers=headers)


def alert_count(pool) -> int:
    return AlertRepository(pool).count()


def test_without_a_key_every_post_creates_a_new_alert(seeded, pool):
    post(seeded, key=None)
    post(seeded, key=None)
    assert alert_count(pool) == 2


def test_replay_returns_the_identical_response_and_creates_nothing(seeded, pool):
    first = post(seeded)
    second = post(seeded)
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()  # same id, same everything
    assert second.headers["location"] == first.headers["location"]
    assert "idempotent-replayed" not in first.headers
    assert second.headers["idempotent-replayed"] == "true"
    assert alert_count(pool) == 1  # only ONE row


def test_replay_is_the_stored_response_not_a_fresh_read(seeded, pool):
    """Even after a new rate arrives, the replay shows what the first request answered."""
    first = post(seeded).json()
    assert (first["current_rate"], first["triggered"]) == ("1.1005", False)
    RateRepository(pool).upsert_many([ExchangeRate(
        rate_date=date(2026, 11, 18), base="EUR", quote="USD", rate=Decimal("1.1300"))])
    assert post(seeded).json() == first  # stored response...
    assert seeded.get(f"/v1/alerts/{first['id']}").json()["triggered"] is True  # ...vs live


def test_same_json_written_differently_is_the_same_request(seeded, pool):
    post(seeded)
    reordered = b'{ "threshold": "1.1200",  "condition": "above", "quote": "USD", "base": "EUR" }'
    resp = seeded.post("/v1/alerts", content=reordered,
                       headers={"Idempotency-Key": KEY, "content-type": "application/json"})
    assert resp.status_code == 201 and resp.headers["idempotent-replayed"] == "true"
    assert alert_count(pool) == 1


def test_same_key_different_body_is_422(seeded, pool):
    post(seeded)
    resp = post(seeded, body={**BODY, "threshold": "1.3000"})
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["type"] == "/problems/idempotency-key-reused"
    assert "errors" not in body
    assert alert_count(pool) == 1


def test_same_key_while_first_is_in_progress_is_409(seeded, pool):
    # Simulate a first request that has claimed the key and is still running.
    IdempotencyRepository(pool).claim(KEY, request_hash(BODY))
    resp = post(seeded)
    assert resp.status_code == 409
    assert resp.json()["type"] == "/problems/idempotency-key-in-use"
    assert alert_count(pool) == 0


def test_different_body_while_in_progress_is_422_not_409(seeded, pool):
    IdempotencyRepository(pool).claim(KEY, request_hash(BODY))
    assert post(seeded, body={**BODY, "quote": "GBP"}).status_code == 422


def test_two_simultaneous_requests_create_one_alert(app, pool, monkeypatch):
    """The real double-click: both arrive before either finishes. One wins, the other gets 409."""
    from tests.api.conftest import seed
    seed(pool)
    original_create = AlertRepository.create

    def slow_create(self, **kwargs):
        time.sleep(0.5)  # keep the first request "in progress" long enough
        return original_create(self, **kwargs)

    monkeypatch.setattr(AlertRepository, "create", slow_create)
    statuses, start = [], threading.Barrier(2)

    def click():
        with TestClient(app) as client:
            start.wait()
            statuses.append(post(client).status_code)

    threads = [threading.Thread(target=click) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(statuses) == [201, 409]
    assert alert_count(pool) == 1


def test_validation_error_releases_the_key(seeded, pool):
    """Nothing ran, so nothing is stored: the user can fix the form and retry with the same key."""
    bad = post(seeded, body={**BODY, "threshold": "-1"})
    assert bad.status_code == 422 and bad.json()["type"] == "/problems/validation-error"
    assert IdempotencyRepository(pool).get(KEY) is None
    fixed = post(seeded)  # same key, corrected body
    assert fixed.status_code == 201 and "idempotent-replayed" not in fixed.headers


def test_crash_releases_the_key(app, pool, monkeypatch):
    from tests.api.conftest import seed
    seed(pool)

    def broken(self, **kwargs):
        raise RuntimeError("database hiccup")

    with monkeypatch.context() as m:
        m.setattr(AlertRepository, "create", broken)
        with TestClient(app, raise_server_exceptions=False) as client:
            assert post(client).status_code == 500
    assert IdempotencyRepository(pool).get(KEY) is None
    with TestClient(app) as client:
        assert post(client).status_code == 201  # the retry works
    assert alert_count(pool) == 1


def test_completed_key_stores_status_and_body(seeded, pool):
    alert = post(seeded).json()
    record = IdempotencyRepository(pool).get(KEY)
    assert (record.status, record.response_status) == ("completed", 201)
    assert record.response_body == alert
    assert record.request_hash == request_hash(BODY)


def test_request_hash_ignores_formatting_but_not_values():
    assert request_hash({"a": 1, "b": "x"}) == request_hash({"b": "x", "a": 1})
    assert request_hash({"a": 1}) != request_hash({"a": 2})
    assert request_hash({"note": None}) != request_hash({})  # null is a value
