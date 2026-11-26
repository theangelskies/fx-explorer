"""Runs ONE import from start to finish.

This file only orchestrates: it asks the client for data, the parsers to clean it, and the
repositories to save it. It contains no SQL and no HTTP details.
"""

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from fx_explorer.backfill import BackfillPlan, plan_backfill
from fx_explorer.frankfurter.client import FrankfurterClient, FrankfurterError
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


@dataclass(frozen=True)
class ChunkResult:
    """Outcome of one backfill request (one GET /rates, one import_runs row)."""

    year: int
    start: dt.date
    end: dt.date
    quotes: tuple[str, ...]
    summary: ImportSummary | None  # None when the chunk failed
    error: str | None = None


@dataclass(frozen=True)
class BackfillSummary:
    plan: BackfillPlan
    chunks: list[ChunkResult] = field(default_factory=list)

    @property
    def failed_years(self) -> list[int]:
        return sorted({c.year for c in self.chunks if c.summary is None})

    @property
    def inserted(self) -> int:
        return sum(c.summary.inserted for c in self.chunks if c.summary)

    @property
    def updated(self) -> int:
        return sum(c.summary.updated for c in self.chunks if c.summary)

    @property
    def unchanged(self) -> int:
        return sum(c.summary.unchanged for c in self.chunks if c.summary)


class Importer:
    def __init__(
        self,
        client: FrankfurterClient | None,  # None only for planning (dry run)
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
        refresh_currencies: bool = True,
    ) -> ImportSummary:
        """Import one day or one range. refresh_currencies=False skips re-downloading the
        currency list (the backfill fetches it once instead of once per chunk)."""
        base = base.strip().upper()
        quotes = [q.strip().upper() for q in quotes]
        params = {
            "base": base, "quotes": quotes,
            "date": date, "from": start, "to": end,
        }
        run_id = self._runs.start(params)
        logger.info("Import run %d started: %s", run_id, {k: v for k, v in params.items() if v})

        try:
            summary = self._import(run_id, base, quotes, date, start, end, refresh_currencies)
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

    def plan_backfill(
        self, base: str, quotes: Sequence[str], start: dt.date, end: dt.date
    ) -> BackfillPlan:
        """Plan using the start dates already stored (no API call). Used by --dry-run too."""
        base = base.strip().upper()
        quotes = [q.strip().upper() for q in quotes]
        start_dates = self._currencies.start_dates([base, *quotes])
        return plan_backfill(base, quotes, start, end, start_dates)

    def run_backfill(
        self, base: str, quotes: Sequence[str], start: dt.date, end: dt.date | None = None
    ) -> BackfillSummary:
        """Import start..end in yearly chunks, one import run per planned request.

        A chunk that still fails after the client's retries is logged and skipped; the
        others continue. Re-running is safe: stored rates are upserted, never duplicated.
        """
        end = end or dt.date.today()
        # Refresh the currency list ONCE, so start dates (and new currencies) are current.
        self._refresh_currencies()
        plan = self.plan_backfill(base, quotes, start, end)
        for note in plan.limits:
            logger.warning("Backfill: %s", note)
        logger.info("Backfill %s %s..%s: %d requests over %d years",
                    plan.base, start, end, len(plan.requests), len(plan.years))

        results: list[ChunkResult] = []
        for req in plan.requests:
            try:
                summary = self.run(plan.base, req.quotes, start=req.start, end=req.end,
                                   refresh_currencies=False)
            except FrankfurterError as exc:
                # run() has already recorded the failure in import_runs.
                logger.error("Backfill year %d (%s..%s) failed, continuing: %s",
                             req.year, req.start, req.end, exc)
                results.append(ChunkResult(req.year, req.start, req.end, req.quotes, None, str(exc)))
            else:
                results.append(ChunkResult(req.year, req.start, req.end, req.quotes, summary))
        return BackfillSummary(plan, results)

    # ------------------------------------------------------------------ internals

    def _refresh_currencies(self) -> int:
        """Download and store the currency list. Returns how many records were rejected."""
        raw_currencies = self._client.get_currencies()
        currencies, currency_rejects = parse_currencies(raw_currencies)
        cur_counts = self._currencies.upsert_many(currencies)
        logger.info(
            "Currencies: %d fetched, %d inserted, %d updated, %d rejected",
            len(raw_currencies), cur_counts.inserted, cur_counts.updated, len(currency_rejects),
        )
        return len(currency_rejects)

    @staticmethod
    def _drop_outside_range(rates, date, start, end):
        """Keep only rates dated inside the range we asked for.

        When a currency has no rate on the first requested day (e.g. NGN on a weekend),
        Frankfurter also returns its last rate BEFORE `from`, e.g. asking for 2017 gives an
        NGN record dated 2016-12-30, with a different value than the 2016 response gave for
        that same day. Storing it would make yearly chunks overwrite each other's boundary
        days on every run. The in-range answer is the real one, so the extra record is dropped.
        These are expected, so they are logged but not counted as rejects.
        """
        low = date or start
        high = date or end
        kept = [r for r in rates
                if (low is None or r.rate_date >= low) and (high is None or r.rate_date <= high)]
        if len(kept) < len(rates):
            logger.info("Ignored %d rate(s) dated outside the requested range %s..%s",
                        len(rates) - len(kept), low, high)
        return kept

    def _import(self, run_id, base, quotes, date, start, end, refresh_currencies) -> ImportSummary:
        # 1. Currencies first: every rate points at two of them (foreign keys).
        currency_rejected = self._refresh_currencies() if refresh_currencies else 0

        # 2. Rates: fetch, clean, save.
        raw_rates = self._client.get_rates(base, quotes, date=date, start=start, end=end)
        valid, rate_rejects = parse_rates(raw_rates, self._currencies.known_codes())
        valid = self._drop_outside_range(valid, date, start, end)
        result = self._rates.upsert_many(valid)

        rejected = len(rate_rejects) + len(result.unknown_currency) + currency_rejected
        status = "partial" if rejected else "success"
        counts = result.counts
        self._runs.finish(
            run_id, status,
            fetched=len(raw_rates), inserted=counts.inserted, updated=counts.updated, rejected=rejected,
        )
        return ImportSummary(
            run_id, status, len(raw_rates), counts.inserted, counts.updated, counts.unchanged, rejected
        )
