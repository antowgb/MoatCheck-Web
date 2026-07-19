#!/usr/bin/env python3
"""One-off backfill: assigns `sector_benchmark_ticker` to active equities that
are missing it, based on the `stocks.sector` string.

Manual/ad-hoc use only — NOT wired into any scheduled workflow (unlike
run_refresh.py). There is no sector->ETF mapping stored anywhere in the
database (the sector ETF rows have `sector` left NULL — Alpha Vantage's
`overview` endpoint, which is where `sector` comes from, is never called for
ETFs, see app/data/fetch.py::refresh_ticker). The mapping below is the same
one that was applied by hand so far via PATCH /stocks/{ticker} (see AMD/ASML/
MSFT/... -> XLK, RDDT -> XLC, ROK/VRT -> XLI in the current data): the 9
SPDR Select Sector ETFs already present in this database, keyed by the GICS
sector name as Alpha Vantage returns it in `overview.Sector`.

Dry-run by default: prints what would change without writing. Pass --apply
to actually write (via the Supabase service_role key, same pattern as
run_refresh.py).
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill_sector_benchmarks")

# GICS sector name (as seen in stocks.sector, i.e. Alpha Vantage's
# `overview.Sector`) -> SPDR Select Sector ETF ticker. Only the 9 sectors
# for which an ETF row already exists in this database are listed here —
# deliberately NOT extended to Real Estate/Utilities (no XLRE/XLU tracked
# yet) or any other taxonomy: a sector with no entry here is left untouched
# and logged, never guessed.
SECTOR_TO_ETF = {
    "TECHNOLOGY": "XLK",
    "INDUSTRIALS": "XLI",
    "COMMUNICATION SERVICES": "XLC",
    "HEALTH CARE": "XLV",
    "HEALTHCARE": "XLV",  # seen as-is in this DB (JNJ, UNH) — Alpha Vantage casing is inconsistent
    "FINANCIALS": "XLF",
    "FINANCIAL SERVICES": "XLF",
    "CONSUMER STAPLES": "XLP",
    "CONSUMER DEFENSIVE": "XLP",
    "CONSUMER DISCRETIONARY": "XLY",
    "CONSUMER CYCLICAL": "XLY",
    "ENERGY": "XLE",
    "MATERIALS": "XLB",
    "BASIC MATERIALS": "XLB",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Actually write to Supabase (default: dry-run, read-only)."
    )
    args = parser.parse_args()

    if args.apply:
        from scripts._service_role import use_service_role_key

        use_service_role_key()

    from app.data.supabase_client import get_supabase

    sb = get_supabase()

    etf_rows = sb.table("stocks").select("ticker").eq("asset_type", "etf").execute().data
    etfs_in_db = {r["ticker"] for r in etf_rows}

    candidates = (
        sb.table("stocks")
        .select("ticker, sector, sector_benchmark_ticker")
        .eq("status", "active")
        .eq("asset_type", "equity")
        .is_("sector_benchmark_ticker", "null")
        .execute()
        .data
    )

    if not candidates:
        logger.info("No active equity with a missing sector_benchmark_ticker. Nothing to do.")
        return 0

    planned: list[tuple[str, str]] = []
    unmatched: list[tuple[str, str | None]] = []

    for row in candidates:
        ticker = row["ticker"]
        sector = row.get("sector")
        etf = SECTOR_TO_ETF.get(sector.strip().upper()) if sector else None

        if etf is None:
            unmatched.append((ticker, sector))
            logger.info("%s: no sector ETF mapping for sector=%r — leaving sector_benchmark_ticker unset.", ticker, sector)
            continue
        if etf not in etfs_in_db:
            unmatched.append((ticker, sector))
            logger.info("%s: mapped ETF %s not found in stocks table — leaving unset.", ticker, etf)
            continue

        planned.append((ticker, etf))
        logger.info("%s (sector=%r) -> %s%s", ticker, sector, etf, "" if args.apply else " [dry-run]")

    if args.apply:
        for ticker, etf in planned:
            sb.table("stocks").update({"sector_benchmark_ticker": etf}).eq("ticker", ticker).execute()
        logger.info("Applied: %d ticker(s) updated, %d left unmatched.", len(planned), len(unmatched))
    else:
        logger.info(
            "Dry-run: %d ticker(s) would be updated, %d would be left unmatched. Re-run with --apply to write.",
            len(planned), len(unmatched),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
