"""Shared fixtures for API tests: the real app on the test database, called in-process."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from fx_explorer.api.app import create_app

CONTRACT = Path(__file__).resolve().parents[2] / "openapi.yaml"


@pytest.fixture(scope="session")
def contract() -> dict:
    """openapi.yaml as a Python dict, for tests that compare code against the contract."""
    return yaml.safe_load(CONTRACT.read_text())


@pytest.fixture
def app(pool):
    return create_app(pool=pool)


@pytest.fixture
def client(app):
    # TestClient calls the app directly in memory: no server or network needed.
    with TestClient(app) as c:
        yield c
