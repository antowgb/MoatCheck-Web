#!/usr/bin/env python3
"""Standalone entrypoint for the V2 qualitative refresh.

Runs the exact same business logic as POST /api/qualitative/refresh, in-process
(no HTTP, no FastAPI), using the Supabase service_role key so RLS doesn't block
writes — same "local machine" architecture as scripts/run_refresh.py.

Independent from the Alpha Vantage refresh: this can run in parallel with it
(separate GitHub Actions job). Only sources enabled in
app/qualitative/config.py::SOURCE_FLAGS actually run (EDGAR only, by default).
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._service_role import use_service_role_key  # noqa: E402

use_service_role_key()

from app.qualitative.run import run_qualitative_refresh  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_qualitative")


def main() -> int:
    logger.info("Starting qualitative refresh (service_role key, local-equivalent run).")
    try:
        summary = run_qualitative_refresh()
    except Exception:
        logger.exception("qualitative refresh crashed")
        return 1
    logger.info("Qualitative refresh summary: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
