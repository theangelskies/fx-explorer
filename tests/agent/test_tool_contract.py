"""Tools must not drift from the contract.

For every tool we look up its endpoint in openapi.yaml and compare the tool's input_schema
with the endpoint's real parameters (or request body). If someone renames a parameter,
changes an enum or removes an endpoint, one of these tests fails.
"""

from pathlib import Path

import pytest
import yaml

from fx_explorer.agent.tools import TOOL_DEFINITIONS, TOOL_ENDPOINTS

SPEC = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi.yaml").read_text())
TOOLS = {t["name"]: t for t in TOOL_DEFINITIONS}


def resolve(node):
    """Replace every {'$ref': '#/components/...'} by what it points to (recursively)."""
    if isinstance(node, dict):
        if "$ref" in node:
            target = SPEC
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target[part]
            return resolve(target)
        return {k: resolve(v) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve(v) for v in node]
    return node


def operation(name: str) -> dict:
    method, path, operation_id = TOOL_ENDPOINTS[name]
    op = SPEC["paths"][path][method.lower()]
    assert op["operationId"] == operation_id
    return op


def assert_schema_matches(tool_schema: dict, api_schema: dict, where: str) -> None:
    for key in ("type", "enum", "pattern", "format"):
        if key in api_schema:
            assert tool_schema.get(key) == api_schema[key], f"{where}: '{key}' differs from the contract"
    for key in ("minimum", "minLength", "minItems"):
        if key in api_schema:
            assert tool_schema.get(key, -1) >= api_schema[key], f"{where}: '{key}' is looser than the contract"
    for key in ("maximum", "maxLength", "maxItems"):
        if key in api_schema:
            assert tool_schema.get(key, 10**9) <= api_schema[key], f"{where}: '{key}' is looser than the contract"
    if "items" in api_schema:
        assert_schema_matches(tool_schema["items"], api_schema["items"], where + "[]")


def test_every_tool_has_a_definition_and_an_endpoint():
    assert set(TOOLS) == set(TOOL_ENDPOINTS)
    for name, tool in TOOLS.items():
        assert tool["description"].strip()
        assert tool["input_schema"]["type"] == "object"
        operation(name)  # the endpoint exists with the expected operationId


@pytest.mark.parametrize("name", [n for n in TOOL_ENDPOINTS if n != "create_alert"])
def test_read_tool_schema_matches_endpoint_parameters(name):
    api_params = {p["name"]: p for p in map(resolve, operation(name)["parameters"])}
    api_params.pop("cursor", None)  # deliberately not offered to the model (see agent-design.md)
    schema = TOOLS[name]["input_schema"]

    for prop, prop_schema in schema["properties"].items():
        assert prop in api_params, f"{name}: '{prop}' is not a parameter of the endpoint"
        assert_schema_matches(prop_schema, api_params[prop]["schema"], f"{name}.{prop}")

    api_required = {n for n, p in api_params.items() if p.get("required")}
    assert api_required <= set(schema["required"]), f"{name}: a required API parameter is optional in the tool"
    assert set(schema["required"]) <= set(schema["properties"])


def test_create_alert_schema_matches_request_body():
    body = resolve(operation("create_alert")["requestBody"]["content"]["application/json"]["schema"])
    schema = TOOLS["create_alert"]["input_schema"]
    for prop, prop_schema in schema["properties"].items():
        assert prop in body["properties"], f"create_alert: '{prop}' is not a field of AlertCreate"
        assert_schema_matches(prop_schema, body["properties"][prop], f"create_alert.{prop}")
    assert set(schema["required"]) == set(body["required"])
    # the tool must offer every field the API accepts, otherwise a confirmed alert loses data
    assert set(schema["properties"]) == set(body["properties"])


def test_the_model_cannot_page_through_cursors():
    for tool in TOOL_DEFINITIONS:
        assert "cursor" not in tool["input_schema"]["properties"]
