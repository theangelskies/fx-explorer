"""GET /v1/health, plus the contract and docs endpoints."""

import pytest
from fastapi.testclient import TestClient

from fx_explorer.api.app import CONTRACT_PATH, create_app
from fx_explorer.db.connection import create_pool

pytestmark = pytest.mark.integration


def test_health_ok(client):
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.json() == {"status": "ok", "database": "ok"}


def test_health_503_when_database_unreachable():
    # Port 1 on localhost: nothing listens there, so every connection attempt fails.
    dead = create_pool("postgresql://nobody:nothing@127.0.0.1:1/none_test", wait=False, timeout=1)
    try:
        with TestClient(create_app(pool=dead)) as client:
            resp = client.get("/v1/health")
    finally:
        dead.close()
    assert resp.status_code == 503
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["type"] == "/problems/service-unavailable"
    assert body["instance"] == "/v1/health"


def test_serves_our_contract_not_a_generated_one(client):
    resp = client.get("/openapi.yaml")
    assert resp.status_code == 200
    assert resp.text == CONTRACT_PATH.read_text()
    # FastAPI's own generated spec and docs must be gone
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_swagger_ui_uses_our_contract(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "/openapi.yaml" in resp.text
