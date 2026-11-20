"""Runs ONE import from start to finish.

This file only orchestrates: it asks the client for data, the parsers to clean it, and the
repositories to save it. It contains no SQL and no HTTP details.
"""

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from fx_explorer.frankfurter.client import FrankfurterClient
from fx_explorer.parsing.currencies import parse_currencies
from fx_explorer.parsing.rates import parse_rates
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.import_runs import ImportRunRepository
from fx_explorer.repositories.rates import RateRepository

logger = logging.getLogger(__name__)

# Rates are blended from several sources and can be revised afterwards, so an incremental
# import goes back a few days before the newest stored date to pick up any revisions.
INCREMENTAL_OVERLAP_DAYS = 3


class NothingToResumeError(Exception):
    """--incremental was used but there is no stored data to continue from."""


@dataclass(frozen=True)
class ImportSummary:
    run_id: int
    status: str  # "success" or "partial" (a failed run raises instead)
    fetched: int  # rate records received from the API
    inserted: int
    updated: int
    unchanged: int
    rejected: int  # records we refused (bad data, unknown currency)


class Importer:
    def __init__(
        self,
        client: FrankfurterClient,
        currencies: CurrencyRepository,
        rates: RateRepository,
        runs: ImportRunRepository,
    ) -> None:
        self._client = client
        self._currencies = currencies
        self._rates = rates
        self._runs = runs

    def run(
        self,
        base: str,
        quotes: Sequence[str],
        *,
        date: dt.date | None = None,
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> ImportSummary:
        base = base.strip().upper()
        quotes = [q.strip().upper() for q in quotes]
        params = {
            "base": base, "quotes": quotes,
            "date": date, "from": start, "to": end,
        }
        run_id = self._runs.start(params)
        logger.info("Import run %d started: %s", run_id, {k: v for k, v in params.items() if v})

        try:
            summary = self._import(run_id, base, quotes, date, start, end)
        except Exception as exc:
            # Record the failure in import_runs, then let the error continue to the caller.
            logger.error("Import run %d FAILED: %s", run_id, exc)
            self._runs.finish(run_id, "failed", error_message=f"{type(exc).__name__}: {exc}")
            raise

        logger.info(
            "Import run %d finished (%s): fetched=%d inserted=%d updated=%d unchanged=%d rejected=%d",
            run_id, summary.status, summary.fetched, summary.inserted,
            summary.updated, summary.unchanged, summary.rejected,
        )
        return summary

    def run_incremental(
        self, base: str, quotes: Sequence[str], today: dt.date | None = None
    ) -> ImportSummary:
        """Import from (newest stored date - 3 days) up to today."""
        base = base.strip().upper()
        latest = self._rates.latest_date(base)
        if latest is None:
            raise NothingToResumeError(
                f"No stored rates for base {base} yet. Do a first import with --from/--to or --date."
            )
        start = latest - dt.timedelta(days=INCREMENTAL_OVERLAP_DAYS)
        return self.run(base, quotes, start=start, end=today or dt.date.today())

    # ------------------------------------------------------------------ internals

    def _import(self, run_id, base, quotes, date, start, end) -> ImportSummary:
        # 1. Currencies first: every rate points at two of them (foreign keys).
        raw_currencies = self._client.get_currencies()
        currencies, currency_rejects = parse_currencies(raw_currencies)
        cur_counts = self._currencies.upsert_many(currencies)
        logger.info(
            "Currencies: %d fetched, %d inserted, %d updated, %d rejected",
            len(raw_currencies), cur_counts.inserted, cur_counts.updated, len(currency_rejects),
        )

        # 2. Rates: fetch, clean, save.
        raw_rates = self._client.get_rates(base, quotes, date=date, start=start, end=end)
        valid, rate_rejects = parse_rates(raw_rates, self._currencies.known_codes())
        result = self._rates.upsert_many(valid)

        rejected = len(rate_rejects) + len(result.unknown_currency) + len(currency_rejects)
        status = "partial" if rejected else "success"
        counts = result.counts
        self._runs.finish(
            run_id, status,
            fetched=len(raw_rates), inserted=counts.inserted, updated=counts.updated, rejected=rejected,
        )
        return ImportSummary(
            run_id, status, len(raw_rates), counts.inserted, counts.updated, counts.unchanged, rejected
        )
