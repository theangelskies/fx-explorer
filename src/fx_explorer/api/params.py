"""Query/path parameter checks that FastAPI can't express by itself.

FastAPI already rejects wrong types, out-of-range numbers and bad enum values (errors.py turns
those into 400s). This module adds what the contract defines on top:
comma-separated lists, strict dates, and "this currency must exist".

Errors are collected first and raised together, so the client sees every bad parameter at
once, one entry per field.
"""

import re
from collections.abc import Sequence
from datetime import date

from fx_explorer.api.errors import ProblemError, field_error

# Same patterns as openapi.yaml. [0-9] rather than \d, because \d also matches non-ASCII
# digits in some regex engines.
CODE_PATTERN = r"^[A-Z]{3,4}$"
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
MAX_QUOTES = 50

CODE_MESSAGE = "Must be a currency code of 3–4 uppercase letters, e.g. USD."
DATE_MESSAGE = "Must be a valid date in YYYY-MM-DD format."


class ParamErrors:
    """Collects parameter problems; raise_if_any() turns them into one 400 Problem."""

    def __init__(self) -> None:
        self._errors: dict[str, str] = {}

    def add(self, field: str, message: str) -> None:
        self._errors.setdefault(field, message)  # keep the first problem per field

    def has(self, field: str) -> bool:
        return field in self._errors

    def raise_if_any(self) -> None:
        if not self._errors:
            return
        n = len(self._errors)
        raise ProblemError(
            "invalid-parameters",
            f"{n} parameter{'' if n == 1 else 's'} {'is' if n == 1 else 'are'} invalid.",
            [field_error(f, m) for f, m in self._errors.items()],
        )


def parse_date(value: str | None, field: str, errors: ParamErrors) -> date | None:
    """'2026-11-13' -> date. The format was already checked by the route's pattern; this
    catches impossible dates like 2026-13-01 or 2026-02-30."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors.add(field, DATE_MESSAGE)
        return None


def parse_codes(value: str | None, field: str, errors: ParamErrors) -> list[str] | None:
    """'USD,GBP' -> ['USD', 'GBP'] (OpenAPI style: form, explode: false). None if absent."""
    if value is None:
        return None
    codes = value.split(",")
    if len(codes) > MAX_QUOTES:
        errors.add(field, f"Must contain at most {MAX_QUOTES} currency codes.")
        return None
    bad = [c for c in codes if not re.fullmatch(CODE_PATTERN, c)]
    if bad:
        errors.add(field, f"{CODE_MESSAGE} Invalid: {', '.join(repr(c) for c in bad[:5])}.")
        return None
    return list(dict.fromkeys(codes))  # drop duplicates, keep order


def check_known(field: str, codes: Sequence[str], known: set[str], errors: ParamErrors) -> None:
    unknown = [c for c in codes if c not in known]
    if unknown:
        label = "currency" if len(unknown) == 1 else "currencies"
        errors.add(field, f"Unknown {label}: {', '.join(unknown)}.")
