"""Turn raw API records into clean ExchangeRate objects.

The golden rule here: ONE bad record must never stop the run. Each record is
checked on its own; a bad one is put in the `rejects` list with a reason, and we
carry on with the next.
"""

import logging
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError

from fx_explorer.domain.models import ExchangeRate
from fx_explorer.frankfurter.schemas import RawRate

logger = logging.getLogger(__name__)

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Reject:
    """A record we refused to store, with the original data and the reason why."""

    raw: Any
    reason: str


def parse_iso_date(text: str) -> date:
    """Strictly YYYY-MM-DD (date.fromisoformat alone would also accept other layouts)."""
    text = text.strip()
    if not _ISO_DATE.fullmatch(text):
        raise ValueError(f"expected YYYY-MM-DD, got {text!r}")
    return date.fromisoformat(text)  # raises ValueError for e.g. 2026-13-45


def describe_validation_error(error: ValidationError) -> str:
    """Turn pydantic's error list into one readable sentence."""
    parts = []
    for item in error.errors():
        field = ".".join(str(p) for p in item["loc"]) or "record"
        if item["type"] == "missing":
            parts.append(f"missing field: {field}")
        else:
            parts.append(f"invalid {field}: {item['msg']}")
    return "; ".join(parts)


def parse_rates(
    raw_records: Iterable[Any],
    known_codes: Collection[str] | None = None,
) -> tuple[list[ExchangeRate], list[Reject]]:
    """Convert raw rate records. Returns (valid_records, rejects).

    known_codes: the currency codes we know about (from the currencies table/API).
    If given, a record with a base or quote outside this set is rejected, because
    the database would refuse it anyway (foreign key). Pass None to skip that check.

    Self-pairs (EUR -> EUR, rate 1) are skipped silently: the API always includes
    them and they are normal, not errors, so they are not counted as rejects.
    """
    known = {code.strip().upper() for code in known_codes} if known_codes is not None else None
    valid: list[ExchangeRate] = []
    rejects: list[Reject] = []

    for raw in raw_records:
        outcome = _parse_one(raw, known)
        if outcome is None:  # self-pair, skipped on purpose
            continue
        if isinstance(outcome, Reject):
            logger.warning("Rejected rate record (%s): %r", outcome.reason, outcome.raw)
            rejects.append(outcome)
        else:
            valid.append(outcome)

    return valid, rejects


def _parse_one(raw: Any, known: set[str] | None) -> ExchangeRate | Reject | None:
    if not isinstance(raw, dict):
        return Reject(raw, f"record is not a JSON object (got {type(raw).__name__})")

    try:
        item = RawRate.model_validate(raw)
    except ValidationError as exc:
        return Reject(raw, describe_validation_error(exc))

    base = item.base.strip().upper()
    quote = item.quote.strip().upper()
    if not base or not quote:
        return Reject(raw, "empty currency code")

    if base == quote:
        return None

    try:
        rate_date = parse_iso_date(item.date)
    except ValueError as exc:
        return Reject(raw, f"invalid date: {exc}")

    if not item.rate.is_finite():
        return Reject(raw, f"rate is not a finite number: {item.rate}")
    if item.rate <= 0:
        return Reject(raw, f"rate must be greater than 0, got {item.rate}")

    if known is not None:
        unknown = [code for code in (base, quote) if code not in known]
        if unknown:
            return Reject(raw, f"unknown currency code: {', '.join(unknown)}")

    return ExchangeRate(rate_date=rate_date, base=base, quote=quote, rate=item.rate)
