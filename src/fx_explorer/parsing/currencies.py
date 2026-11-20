"""Turn raw /currencies records into clean Currency objects (same reject idea as rates)."""

import logging
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from fx_explorer.domain.models import Currency
from fx_explorer.frankfurter.schemas import RawCurrency
from fx_explorer.parsing.rates import Reject, describe_validation_error, parse_iso_date

logger = logging.getLogger(__name__)


def parse_currencies(raw_records: Iterable[Any]) -> tuple[list[Currency], list[Reject]]:
    valid: list[Currency] = []
    rejects: list[Reject] = []

    for raw in raw_records:
        try:
            if not isinstance(raw, dict):
                raise ValueError(f"record is not a JSON object (got {type(raw).__name__})")
            item = RawCurrency.model_validate(raw)
            valid.append(
                Currency(
                    iso_code=item.iso_code,
                    iso_numeric=item.iso_numeric,
                    name=item.name,
                    symbol=item.symbol,
                    type=item.type,  # type: ignore[arg-type]  # the model checks the allowed values
                    start_date=parse_iso_date(item.start_date) if item.start_date else None,
                    end_date=parse_iso_date(item.end_date) if item.end_date else None,
                )
            )
        except ValidationError as exc:
            rejects.append(Reject(raw, describe_validation_error(exc)))
        except ValueError as exc:
            rejects.append(Reject(raw, str(exc)))

    for reject in rejects:
        logger.warning("Rejected currency record (%s): %r", reject.reason, reject.raw)
    return valid, rejects
