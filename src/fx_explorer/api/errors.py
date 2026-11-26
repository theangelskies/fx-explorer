"""Turn every error into an RFC 9457 Problem Details response (docs/decisions/0002).

Whatever goes wrong (our own checks, FastAPI's validation, an unknown URL, a database outage,
a bug), the client always gets `application/problem+json` with type/title/status/detail/instance
and, for validation problems, `errors: [{field, message}]`.
"""

import logging
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"

# type -> (status, title). The titles never change for a type; `detail` explains each case.
# Keep in sync with the Problem.type enum in openapi.yaml.
PROBLEM_TYPES: dict[str, tuple[int, str]] = {
    "invalid-parameters": (400, "Invalid query or path parameters"),
    "not-found": (404, "Resource not found"),
    "method-not-allowed": (405, "Method not allowed"),
    "idempotency-key-in-use": (409, "Request with this idempotency key is in progress"),
    "validation-error": (422, "Request body is invalid"),
    "idempotency-key-reused": (422, "Idempotency key reused with a different request"),
    "internal-error": (500, "Internal server error"),
    "service-unavailable": (503, "Service unavailable"),
}


class ProblemError(Exception):
    """Raise this from a route to return a specific Problem, e.g.

    raise ProblemError("not-found", "No currency with code 'XYZ'.")
    """

    def __init__(
        self,
        problem_type: str,
        detail: str,
        errors: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if problem_type not in PROBLEM_TYPES:
            raise ValueError(f"unknown problem type {problem_type!r}")
        super().__init__(detail)
        self.problem_type = problem_type
        self.detail = detail
        self.errors = errors
        self.headers = headers


def field_error(field: str, message: str) -> dict[str, str]:
    return {"field": field, "message": message}


def problem_response(
    request: Request,
    problem_type: str,
    detail: str,
    errors: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    status, title = PROBLEM_TYPES[problem_type]
    body: dict[str, Any] = {
        "type": f"/problems/{problem_type}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


# --------------------------------------------------------------- validation errors
# FastAPI validates parameters and bodies with pydantic and raises RequestValidationError.
# Each pydantic error has a `loc` (where: ("query", "limit"), ("body", "threshold") ...),
# a `type` (what kind of mistake) and a `ctx` with details. We turn those into one short,
# human sentence per field instead of exposing pydantic's internal wording.

def _friendly_message(err: dict[str, Any]) -> str:
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    if kind == "missing":
        return "This field is required."
    if kind in ("int_parsing", "int_type", "int_from_float"):
        return "Must be a whole number."
    if kind == "greater_than_equal":
        return f"Must be at least {ctx.get('ge')}."
    if kind == "less_than_equal":
        return f"Must be at most {ctx.get('le')}."
    if kind == "greater_than":
        return f"Must be greater than {ctx.get('gt')}."
    if kind in ("bool_parsing", "bool_type"):
        return "Must be true or false."
    if kind in ("date_parsing", "date_from_datetime_parsing", "date_from_datetime_inexact", "date_type"):
        return "Must be a valid date in YYYY-MM-DD format."
    if kind in ("uuid_parsing", "uuid_type", "uuid_version"):
        return "Must be a valid UUID."
    if kind in ("literal_error", "enum"):
        return f"Must be one of: {ctx.get('expected', '').replace(chr(39), '')}."
    if kind == "string_too_short":
        return "Must not be empty." if ctx.get("min_length") == 1 else \
            f"Must be at least {ctx.get('min_length')} characters."
    if kind == "string_too_long":
        return f"Must be at most {ctx.get('max_length')} characters."
    if kind in ("too_short", "too_long"):
        return f"Must contain between {ctx.get('min_length', 1)} and {ctx.get('max_length')} items."
    if kind == "string_type":
        return "Must be a string."
    if kind == "extra_forbidden":
        return "Unknown field."
    if kind == "json_invalid":
        return "Request body is not valid JSON."
    if kind in ("model_attributes_type", "dict_type", "model_type"):
        return "Request body must be a JSON object."
    if kind == "value_error":
        # Raised by our own validators: their message is already written for people.
        return str(ctx.get("error", err.get("msg", "Invalid value.")))
    return str(err.get("msg", "Invalid value."))


def _field_name(loc: tuple[Any, ...]) -> str:
    """("query", "limit") -> "limit"; ("body", "threshold") -> "threshold"; ("body",) -> "body"."""
    rest = [str(part) for part in loc[1:]]
    return ".".join(rest) if rest else "body"


async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    raw = list(exc.errors())
    # Parameters (query/path/header) -> 400; body -> 422. A malformed URL is reported first.
    param_errors = [e for e in raw if e["loc"] and e["loc"][0] in ("query", "path", "header")]
    chosen = param_errors or raw
    problem_type = "invalid-parameters" if param_errors else "validation-error"

    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for e in chosen:
        # json_invalid's loc holds a character position, not a field: the whole body is bad.
        field = "body" if e.get("type") == "json_invalid" else _field_name(tuple(e["loc"]))
        if field in seen:  # one entry per field: the first problem is enough to fix it
            continue
        seen.add(field)
        errors.append(field_error(field, _friendly_message(e)))

    n = len(errors)
    what = "parameter" if param_errors else "field"
    detail = f"{n} {what}{'' if n == 1 else 's'} {'is' if n == 1 else 'are'} invalid."
    return problem_response(request, problem_type, detail, errors)


# ---------------------------------------------------------------- other handlers

async def _problem_handler(request: Request, exc: ProblemError) -> JSONResponse:
    return problem_response(request, exc.problem_type, exc.detail, exc.errors, exc.headers)


async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Starlette's own errors: mainly unknown routes (404) and wrong methods (405)."""
    if exc.status_code == 404:
        return problem_response(
            request, "not-found", f"No resource at {request.method} {request.url.path}.")
    if exc.status_code == 405:
        return problem_response(
            request, "method-not-allowed",
            f"{request.method} is not allowed on {request.url.path}.",
            headers=exc.headers,  # keeps the Allow header, which lists the allowed methods
        )
    if exc.status_code < 500:
        return problem_response(request, "invalid-parameters", str(exc.detail))
    return problem_response(request, "internal-error", "Something went wrong on our side.")


async def _database_down_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.warning("Database unavailable on %s %s: %s", request.method, request.url.path, exc)
    return problem_response(request, "service-unavailable", "The database cannot be reached.")


async def _unexpected_handler(request: Request, exc: Exception) -> JSONResponse:
    # Log the full traceback for us; tell the client nothing internal.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return problem_response(
        request, "internal-error", "Something went wrong on our side. Please try again later.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProblemError, _problem_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_handler)
    app.add_exception_handler(PoolTimeout, _database_down_handler)
    app.add_exception_handler(psycopg.OperationalError, _database_down_handler)
    app.add_exception_handler(Exception, _unexpected_handler)
