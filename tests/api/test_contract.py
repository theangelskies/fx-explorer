"""Contract tests: schemathesis reads openapi.yaml, generates many requests (valid and invalid)
for each operation, sends them to the real app, and checks every response against the contract:
status code documented, content type, headers, and body schema.

Add each operationId to IMPLEMENTED as its endpoint is built. Until then it is left out,
because an unbuilt endpoint returns a 404 the contract doesn't promise.
"""

import pytest
import schemathesis

from fx_explorer.api.app import create_app

pytestmark = pytest.mark.integration

IMPLEMENTED = ["getHealth"]


@pytest.fixture
def api_schema(pool):
    app = create_app(pool=pool)
    # Load the contract through the app itself (GET /openapi.yaml): this also proves the
    # app serves exactly the file schemathesis tests against.
    return schemathesis.openapi.from_asgi("/openapi.yaml", app)


# The filter must be applied here, on the lazily-loaded schema (a filter set inside the
# fixture is ignored).
schema = schemathesis.pytest.from_fixture("api_schema").include(operation_id=IMPLEMENTED)


@schema.parametrize()
def test_api_matches_contract(case):
    case.call_and_validate()
