"""Command line entry point:  uv run python -m fx_explorer import ...

Examples:
  import --base EUR --quotes USD,GBP --date 2026-11-20
  import --from 2026-11-01 --to 2026-11-20
  import --incremental
  import --backfill --from 2016-01-01            (yearly chunks up to today)
  import --backfill --from 2016-01-01 --dry-run  (show the plan, call nothing)
"""

import argparse
import datetime as dt
import logging
import sys
from collections.abc import Sequence

from fx_explorer.agent import cli as agent_cli
from fx_explorer.config import get_settings
from fx_explorer.db.connection import create_pool
from fx_explorer.frankfurter.client import FrankfurterClient, FrankfurterError
from fx_explorer.backfill import BackfillPlan
from fx_explorer.importer import BackfillSummary, Importer, NothingToResumeError
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.import_runs import ImportRunRepository
from fx_explorer.repositories.rates import RateRepository


def _iso_date(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a valid date (use YYYY-MM-DD): {text!r}") from None


def _codes(text: str) -> list[str]:
    return [c.strip().upper() for c in text.split(",") if c.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fx_explorer", description="Frankfurter rates importer")
    sub = parser.add_subparsers(dest="command", required=True)
    agent_cli.add_parsers(sub)

    imp = sub.add_parser("import", help="download rates and store them in PostgreSQL")
    imp.add_argument("--base", help="base currency (default: DEFAULT_BASE from .env)")
    imp.add_argument("--quotes", type=_codes, help="comma-separated quotes, e.g. USD,GBP (default: DEFAULT_QUOTES)")
    imp.add_argument("--date", type=_iso_date, help="one day, YYYY-MM-DD")
    imp.add_argument("--from", dest="start", type=_iso_date, help="range start, YYYY-MM-DD")
    imp.add_argument("--to", dest="end", type=_iso_date, help="range end, YYYY-MM-DD (default: today)")
    imp.add_argument("--incremental", action="store_true",
                     help="continue from the newest stored date (minus 3 days overlap) until today")
    imp.add_argument("--backfill", action="store_true",
                     help="import --from..--to (default today) in yearly chunks, "
                          "never before each currency's start_date")
    imp.add_argument("--dry-run", action="store_true",
                     help="with --backfill: print the planned requests without calling the API")
    return parser


def print_plan(plan: BackfillPlan) -> None:
    print(f"Backfill plan: base {plan.base}, {plan.start} .. {plan.end}")
    for note in plan.limits:
        print(f"  note: {note}")
    for req in plan.requests:
        print(f"  {req.year}  {req.describe(plan.base)}")
    print(f"{len(plan.requests)} requests over {len(plan.years)} years.")


def print_backfill_summary(summary: BackfillSummary, base: str, quotes: Sequence[str]) -> None:
    print()
    print(f"{'year':<6}{'from':<12}{'to':<12}{'status':<9}{'fetched':>8}{'inserted':>9}"
          f"{'updated':>8}{'unchanged':>10}")
    for c in summary.chunks:
        s = c.summary
        if s is None:
            print(f"{c.year:<6}{c.start!s:<12}{c.end!s:<12}{'FAILED':<9}  {c.error}")
        else:
            print(f"{c.year:<6}{c.start!s:<12}{c.end!s:<12}{s.status:<9}{s.fetched:>8}"
                  f"{s.inserted:>9}{s.updated:>8}{s.unchanged:>10}")
    print(f"Total: inserted={summary.inserted} updated={summary.updated} "
          f"unchanged={summary.unchanged}")
    if summary.failed_years:
        print(f"FAILED years: {', '.join(map(str, summary.failed_years))}. Re-run each with:")
        for year in summary.failed_years:
            chunk = [c for c in summary.chunks if c.year == year]
            start = min(c.start for c in chunk)
            end = max(c.end for c in chunk)
            print(f"  uv run python -m fx_explorer import --backfill --base {base} "
                  f"--quotes {','.join(quotes)} --from {start} --to {end}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in ("ask", "chat"):  # the "Ask your data" agent (Week 3)
        return agent_cli.run(args)

    # Options that cannot be combined.
    if args.incremental and (args.date or args.start or args.end):
        parser.error("--incremental cannot be combined with --date, --from or --to")
    if args.date and (args.start or args.end):
        parser.error("--date cannot be combined with --from/--to")
    if args.end and not args.start:
        parser.error("--to needs --from")
    if args.start and args.end and args.start > args.end:
        parser.error("--from must not be after --to")
    if args.backfill and (args.incremental or args.date):
        parser.error("--backfill cannot be combined with --incremental or --date")
    if args.backfill and not args.start:
        parser.error("--backfill needs --from")
    if args.dry_run and not args.backfill:
        parser.error("--dry-run only works with --backfill")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too noisy

    settings = get_settings()
    base = args.base or settings.default_base
    quotes = args.quotes or settings.default_quotes

    pool = create_pool()
    # A dry run must not touch the API, so it gets no HTTP client at all.
    client = None if args.dry_run else FrankfurterClient.from_settings(settings)
    try:
        importer = Importer(
            client, CurrencyRepository(pool), RateRepository(pool), ImportRunRepository(pool)
        )
        if args.dry_run:
            plan = importer.plan_backfill(base, quotes, args.start, args.end or dt.date.today())
            print_plan(plan)
            print("Dry run: no API calls made, nothing written.")
        elif args.backfill:
            summary = importer.run_backfill(base, quotes, args.start, args.end)
            print_backfill_summary(summary, base, quotes)
            if summary.failed_years:
                return 1
        elif args.incremental:
            importer.run_incremental(base, quotes)
        else:
            importer.run(base, quotes, date=args.date, start=args.start, end=args.end)
    except NothingToResumeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except FrankfurterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
