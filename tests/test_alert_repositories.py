"""Integration tests: alerts and idempotency-key storage (real PostgreSQL)."""

import threading
import uuid
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from fx_explorer.domain.models import Currency, ExchangeRate
from fx_explorer.repositories.alerts import AlertRepository
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.idempotency import IdempotencyRepository
from fx_explorer.repositories.rates import RateRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def alerts(pool):
    CurrencyRepository(pool).upsert_many(
        [Currency(iso_code=c, name=c, type="currency") for c in ("EUR", "USD", "GBP")]
    )
    RateRepository(pool).upsert_many([
        ExchangeRate(rate_date=date(2026, 11, 16), base="EUR", quote="USD", rate=Decimal("1.1034")),
        ExchangeRate(rate_date=date(2026, 11, 17), base="EUR", quote="USD", rate=Decimal("1.1005")),
    ])
    return AlertRepository(pool)


def make(alerts, condition="above", threshold="1.1200", **kw):
    return alerts.create(base=kw.get("base", "EUR"), quote=kw.get("quote", "USD"),
                         condition=condition, threshold=Decimal(threshold), note=kw.get("note"))


# -------------------------------------------------------------------- alerts

def test_create_and_get(alerts):
    a = make(alerts, note="Buy dollars for the trip")
    assert isinstance(a.id, uuid.UUID)
    assert (a.base, a.quote, a.condition, a.note) == ("EUR", "USD", "above", "Buy dollars for the trip")
    assert a.threshold == Decimal("1.1200") and str(a.threshold) == "1.1200"  # scale kept
    assert a.created_at.tzinfo is not None
    assert a.current_rate == Decimal("1.1005")  # the LATEST stored rate, not the 16th
    assert alerts.get(a.id) == a


@pytest.mark.parametrize(
    ("condition", "threshold", "triggered"),
    [
        ("above", "1.1200", False),
        ("above", "1.1005", True),   # reaching the threshold counts
        ("above", "1.0000", True),
        ("below", "1.0000", False),
        ("below", "1.1005", True),
        ("below", "1.2000", True),
    ],
)
def test_triggered(alerts, condition, threshold, triggered):
    assert make(alerts, condition, threshold).triggered is triggered


def test_no_current_rate_means_not_triggered(alerts):
    a = make(alerts, "below", "9999", base="USD", quote="GBP")  # no USD/GBP rates stored
    assert a.current_rate is None and a.triggered is False


def test_triggered_follows_new_imports(alerts, pool):
    a = make(alerts, "above", "1.1100")
    assert a.triggered is False
    RateRepository(pool).upsert_many([ExchangeRate(
        rate_date=date(2026, 11, 18), base="EUR", quote="USD", rate=Decimal("1.1150"))])
    again = alerts.get(a.id)
    assert again.current_rate == Decimal("1.1150") and again.triggered is True


def test_list_pages_newest_first_and_filter(alerts):
    created = [make(alerts, "above", t) for t in ("1.0", "1.2", "1.05", "1.3", "1.1")]
    triggered_ids = {a.id for a in created if a.triggered}  # rate 1.1005 reached 1.0, 1.05, 1.1
    assert len(triggered_ids) == 3

    seen, after = [], None
    while True:
        page = alerts.list_page(limit=2, after=after)
        seen += page.items
        if not page.has_more:
            break
        after = (page.items[-1].created_at, page.items[-1].id)
    assert len(seen) == 5 and len({a.id for a in seen}) == 5
    order = [(a.created_at, a.id) for a in seen]
    assert order == sorted(order, reverse=True)

    assert {a.id for a in alerts.list_page(limit=50, triggered=True).items} == triggered_ids
    waiting = alerts.list_page(limit=50, triggered=False).items
    assert len(waiting) == 2 and not any(a.triggered for a in waiting)


def test_delete(alerts):
    a = make(alerts)
    assert alerts.delete(a.id) is True
    assert alerts.get(a.id) is None
    assert alerts.delete(a.id) is False
    assert alerts.get(uuid.uuid4()) is None


@pytest.mark.parametrize(
    ("sql_values", "why"),
    [
        ("('EUR', 'EUR', 'above', 1)", "base = quote"),
        ("('EUR', 'USD', 'sideways', 1)", "bad condition"),
        ("('EUR', 'USD', 'above', 0)", "threshold not > 0"),
        ("('EUR', 'USD', 'above', 0.00000000001)", "more than 10 decimal places"),
        ("('EUR', 'XYZ', 'above', 1)", "unknown currency"),
    ],
)
def test_database_rejects_bad_alerts(alerts, pool, sql_values, why):
    """The CHECKs and FKs are the last line of defence if the API validation had a bug."""
    with pytest.raises(psycopg.errors.IntegrityError), pool.connection() as conn:
        conn.execute(f"INSERT INTO rate_alerts (base, quote, condition, threshold) VALUES {sql_values}")


def test_database_rejects_long_note(alerts, pool):
    with pytest.raises(psycopg.errors.CheckViolation), pool.connection() as conn:
        conn.execute("INSERT INTO rate_alerts (base, quote, condition, threshold, note) "
                     "VALUES ('EUR', 'USD', 'above', 1, %s)", ("x" * 201,))


# ----------------------------------------------------------- idempotency keys

@pytest.fixture
def keys(pool):
    return IdempotencyRepository(pool)


def test_claim_complete_replay(keys):
    assert keys.claim("k1", "hash-a").claimed is True
    second = keys.claim("k1", "hash-a")
    assert second.claimed is False
    assert (second.existing.status, second.existing.response_status) == ("in_progress", None)

    keys.complete("k1", 201, {"id": "abc", "threshold": "1.1200"})
    third = keys.claim("k1", "hash-a")
    assert third.existing.status == "completed"
    assert third.existing.response_status == 201
    assert third.existing.response_body == {"id": "abc", "threshold": "1.1200"}
    assert third.existing.request_hash == "hash-a"


def test_release_frees_key(keys):
    keys.claim("k2", "h")
    keys.release("k2")
    assert keys.get("k2") is None
    assert keys.claim("k2", "h").claimed is True


def test_release_never_deletes_completed_key(keys):
    keys.claim("k3", "h")
    keys.complete("k3", 201, {})
    keys.release("k3")
    assert keys.get("k3").status == "completed"


def test_completed_key_needs_response(pool):
    with pytest.raises(psycopg.errors.CheckViolation), pool.connection() as conn:
        conn.execute("INSERT INTO idempotency_keys (key, request_hash, status) "
                     "VALUES ('k', 'h', 'completed')")


def test_concurrent_claims_only_one_wins(keys):
    """Ten requests with the same key at the same moment: exactly one may proceed."""
    results, barrier = [], threading.Barrier(10)

    def worker():
        barrier.wait()
        results.append(keys.claim("race", "h").claimed)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1 and results.count(False) == 9


def test_stale_in_progress_key_can_be_taken_over(keys, pool):
    """A server that crashed mid-request leaves 'in_progress' behind; after 60 s it's free."""
    keys.claim("crashed", "old-hash")
    assert keys.claim("crashed", "new-hash").claimed is False  # fresh: still in progress
    with pool.connection() as conn:
        conn.execute("UPDATE idempotency_keys SET created_at = now() - interval '61 seconds' "
                     "WHERE key = 'crashed'")
    assert keys.claim("crashed", "new-hash").claimed is True
    assert keys.get("crashed").request_hash == "new-hash"


def test_completed_key_is_never_taken_over(keys, pool):
    keys.claim("done", "h")
    keys.complete("done", 201, {"id": "x"})
    with pool.connection() as conn:
        conn.execute("UPDATE idempotency_keys SET created_at = now() - interval '1 day'")
    claim = keys.claim("done", "other")
    assert claim.claimed is False and claim.existing.status == "completed"
