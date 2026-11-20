"""Command line entry point:  uv run python -m fx_explorer import ...

Examples:
  import --base EUR --quotes USD,GBP --date 2026-11-20
  import --from 2026-11-01 --to 2026-11-20
  import --incremental
"""

import argparse
import datetime as dt
import logging
import sys
from collections.abc import Sequence

from fx_explorer.config import get_settings
from fx_explorer.db.connection import create_pool
from fx_explorer.frankfurter.client import FrankfurterClient, FrankfurterError
from fx_explorer.importer import Importer, NothingToResumeError
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

    imp = sub.add_parser("import", help="download rates and store them in PostgreSQL")
    imp.add_argument("--base", help="base currency (default: DEFAULT_BASE from .env)")
    imp.add_argument("--quotes", type=_codes, help="comma-separated quotes, e.g. USD,GBP (default: DEFAULT_QUOTES)")
    imp.add_argument("--date", type=_iso_date, help="one day, YYYY-MM-DD")
    imp.add_argument("--from", dest="start", type=_iso_date, help="range start, YYYY-MM-DD")
    imp.add_argument("--to", dest="end", type=_iso_date, help="range end, YYYY-MM-DD (default: today)")
    imp.add_argument("--incremental", action="store_true",
                     help="continue from the newest stored date (minus 3 days overlap) until today")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Options that cannot be combined.
    if args.incremental and (args.date or args.start or args.end):
        parser.error("--incremental cannot be combined with --date, --from or --to")
    if args.date and (args.start or args.end):
        parser.error("--date cannot be combined with --from/--to")
    if args.end and not args.start:
        parser.error("--to needs --from")
    if args.start and args.end and args.start > args.end:
        parser.error("--from must not be after --to")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too noisy

    settings = get_settings()
    base = args.base or settings.default_base
    quotes = args.quotes or settings.default_quotes

    pool = create_pool()
    client = FrankfurterClient.from_settings(settings)
    try:
        importer = Importer(
            client, CurrencyRepository(pool), RateRepository(pool), ImportRunRepository(pool)
        )
        if args.incremental:
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
        client.close()
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
