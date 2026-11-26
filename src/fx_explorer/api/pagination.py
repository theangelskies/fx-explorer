"""Opaque cursors for keyset pagination (docs/decisions/0003-cursor-pagination.md).

A cursor is the sort-key values of the last row on a page, as compact JSON, base64url-encoded
without padding, e.g. {"d":"2026-11-15","q":"USD"} -> "eyJkIjoiMjAyNi0xMS0xNSIsInEiOiJVU0QifQ".

Clients must treat it as an opaque token: we may change what's inside at any time. Anything
that doesn't decode to exactly the keys we expect is rejected with a 400, never a 500.
"""

import base64
import json

from fx_explorer.api.errors import ProblemError, field_error

INVALID_CURSOR_MESSAGE = "Invalid cursor. Start again from the first page without a cursor."


def invalid_cursor() -> ProblemError:
    return ProblemError(
        "invalid-parameters", "1 parameter is invalid.",
        [field_error("cursor", INVALID_CURSOR_MESSAGE)],
    )


def encode_cursor(values: dict[str, str]) -> str:
    raw = json.dumps(values, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str, keys: set[str]) -> dict[str, str]:
    """Return the cursor's values, or raise a 400 Problem if it isn't one of ours."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)  # base64 needs length % 4 == 0
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except ValueError:  # bad base64, bad UTF-8, bad JSON, non-ASCII: all subclasses of ValueError
        raise invalid_cursor() from None
    if (
        not isinstance(data, dict)
        or set(data) != keys
        or not all(isinstance(v, str) for v in data.values())
    ):
        raise invalid_cursor()
    return data
