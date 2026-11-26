"""The Idempotency-Key pattern for POST /v1/alerts (docs/decisions/0006-idempotent-alert-creation.md).

A client sends the same `Idempotency-Key` header when it RETRIES the same action. Then:

  no key                       -> normal behaviour (not handled here)
  new key                      -> claim it ('in_progress'), do the work, store the response
  same key, same body, done    -> return the STORED response + `Idempotent-Replayed: true`
  same key, different body     -> 422 idempotency-key-reused (a key was reused for another action)
  same key, first still running-> 409 idempotency-key-in-use (retry in a moment)

"Same body" is decided by a SHA-256 hash of the body as canonical JSON (keys sorted, no
whitespace), so formatting and key order don't matter, but every value does.

Following Stripe: only a request that actually ran is stored. If validation fails or the server
crashes, the caller releases the key, so the client can fix the body / retry with the same key.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi.responses import JSONResponse

from fx_explorer.api.errors import ProblemError
from fx_explorer.repositories.idempotency import IdempotencyRepository

REPLAYED_HEADER = "Idempotent-Replayed"


def request_hash(payload: Any) -> str:
    """Fingerprint of a request body: equal for equal JSON, whatever the formatting."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def begin(
    keys: IdempotencyRepository,
    key: str,
    payload: Any,
    location_for: Callable[[dict[str, Any]], str],
) -> JSONResponse | None:
    """Claim `key` for this request.

    Returns None when the key is new and now ours: the caller must do the work, then
    keys.complete(...) on success or keys.release(...) on failure.
    Returns a JSONResponse (the stored replay) when the same request already completed.
    Raises a 422 or 409 Problem otherwise.
    """
    fingerprint = request_hash(payload)
    claim = keys.claim(key, fingerprint)
    if claim.claimed:
        return None

    record = claim.existing
    # Check the body first: a different body is a client bug whether or not the first
    # request has finished.
    if record.request_hash != fingerprint:
        raise ProblemError(
            "idempotency-key-reused",
            "This Idempotency-Key was already used with a different request body. "
            "Use a new key for a new request.",
        )
    if record.status == "in_progress":
        raise ProblemError(
            "idempotency-key-in-use",
            "A request with this Idempotency-Key is still being processed. Retry in a moment.",
        )
    # Completed: send back exactly what the first request got.
    return JSONResponse(
        content=record.response_body,
        status_code=record.response_status,
        headers={"Location": location_for(record.response_body), REPLAYED_HEADER: "true"},
    )
