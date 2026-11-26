"""Every error must be a Problem Details response in the contract's shape (ADR 0002).

Phase 4 has no endpoints with parameters or bodies yet, so we bolt a few tiny test-only
routes onto the real app to exercise each error handler.
"""

from datetime import date

import pytest
from fastapi import Query
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, ConfigDict, Field

from fx_explorer.api.errors import PROBLEM_TYPES, ProblemError

pytestmark = pytest.mark.integration

PROBLEM_KEYS = {"type", "title", "status", "detail", "instance"}


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=3)
    n: int


@pytest.fixture
def client(app):
    @app.get("/test/params")
    def params(limit: int = Query(50, ge=1, le=200), day: date | None = None):
        return {"ok": True}

    @app.post("/test/body")
    def body(b: Body):
        return {"ok": True}

    @app.get("/test/problem")
    def problem():
        raise ProblemError("not-found", "No currency with code 'XYZ'.")

    @app.get("/test/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    @app.get("/test/db-down")
    def db_down():
        raise PoolTimeout("couldn't get a connection after 5.00 sec")

    # raise_server_exceptions=False: behave like a real server on a crash (return the 500)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def assert_problem(resp, status: int, type_: str) -> dict:
    assert resp.status_code == status
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert PROBLEM_KEYS <= body.keys()
    assert body["status"] == status
    assert body["type"] == f"/problems/{type_}"
    assert body["title"] == PROBLEM_TYPES[type_][1]
    return body


def test_unknown_route_is_404_problem(client):
    body = assert_problem(client.get("/v1/nope"), 404, "not-found")
    assert body["instance"] == "/v1/nope"
    assert "errors" not in body


def test_wrong_method_is_405_problem_with_allow_header(client):
    resp = client.delete("/v1/health")
    assert_problem(resp, 405, "method-not-allowed")
    assert resp.headers["allow"] == "GET"


@pytest.mark.parametrize(
    ("query", "field", "message"),
    [
        ("limit=0", "limit", "Must be at least 1."),
        ("limit=201", "limit", "Must be at most 200."),
        ("limit=abc", "limit", "Must be a whole number."),
        ("day=2026-13-01", "day", "Must be a valid date in YYYY-MM-DD format."),
    ],
)
def test_bad_query_parameter_is_400_with_field_error(client, query, field, message):
    body = assert_problem(client.get(f"/test/params?{query}"), 400, "invalid-parameters")
    assert body["errors"] == [{"field": field, "message": message}]
    assert body["detail"] == "1 parameter is invalid."


def test_several_bad_parameters_one_entry_each(client):
    body = assert_problem(client.get("/test/params?limit=0&day=x"), 400, "invalid-parameters")
    assert {e["field"] for e in body["errors"]} == {"limit", "day"}
    assert body["detail"] == "2 parameters are invalid."


def test_bad_body_is_422_with_field_errors(client):
    resp = client.post("/test/body", json={"name": "toolong", "extra": 1})
    body = assert_problem(resp, 422, "validation-error")
    assert body["errors"] == [
        {"field": "name", "message": "Must be at most 3 characters."},
        {"field": "n", "message": "This field is required."},
        {"field": "extra", "message": "Unknown field."},
    ]


def test_unparseable_body_is_422_on_field_body(client):
    resp = client.post("/test/body", content=b"{not json", headers={"content-type": "application/json"})
    body = assert_problem(resp, 422, "validation-error")
    assert body["errors"][0]["field"] == "body"


def test_raised_problem(client):
    body = assert_problem(client.get("/test/problem"), 404, "not-found")
    assert body["detail"] == "No currency with code 'XYZ'."


def test_crash_is_500_problem_without_internals(client):
    resp = client.get("/test/boom")
    body = assert_problem(resp, 500, "internal-error")
    assert "secret" not in resp.text


def test_database_timeout_is_503_problem(client):
    assert_problem(client.get("/test/db-down"), 503, "service-unavailable")


def test_problem_types_match_contract(contract):
    """errors.py and openapi.yaml must list exactly the same problem types."""
    in_contract = set(contract["components"]["schemas"]["Problem"]["properties"]["type"]["enum"])
    assert in_contract == {f"/problems/{t}" for t in PROBLEM_TYPES}
