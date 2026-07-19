#!/usr/bin/env python3
"""Standalone entrypoint for the daily refresh + score recompute pipeline.

Runs the exact same business logic as POST /api/refresh (auto mode) and
POST /api/score/recompute, in-process (no HTTP, no FastAPI), using the
Supabase service_role key so RLS doesn't block writes.

Render only ever holds the anon (read-only) key — see the RLS section of
supabase_schema.sql ("write locally (service_role), read on Render (anon)").
This script is
the "local machine" in that architecture; it must run from a trusted
environment (GitHub Actions with the service_role key in secrets), never
from the Render web service.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._service_role import use_service_role_key  # noqa: E402

use_service_role_key()

from app.api.routes import recompute_scores  # noqa: E402
from app.data.fetch import refresh_due  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_refresh")


def main() -> int:
    logger.info("Starting refresh pipeline (service_role key, local-equivalent run).")

    refresh_results = refresh_due()
    ok_count = sum(1 for r in refresh_results if r.get("ok"))
    quota_hit = any(r.get("quota_exhausted") for r in refresh_results)
    real_failures = [r for r in refresh_results if not r.get("ok") and not r.get("quota_exhausted")]

    logger.info(
        "Refresh done: %d/%d ticker(s) ok, quota_exhausted=%s, real_failures=%d",
        ok_count, len(refresh_results), quota_hit, len(real_failures),
    )
    for r in real_failures:
        logger.error("Refresh failure: %s -> %s", r.get("ticker"), r.get("error"))

    try:
        recompute_result = recompute_scores()
    except Exception:
        logger.exception("score/recompute crashed")
        return 1

    scored = [r for r in recompute_result["results"] if "composite_score" in r]
    skipped = [r for r in recompute_result["results"] if r.get("skipped")]
    logger.info("Recompute done: %d ticker(s) scored, %d skipped (ETF).", len(scored), len(skipped))

    if real_failures:
        logger.error("Exiting non-zero: %d real refresh failure(s).", len(real_failures))
        return 1

    if quota_hit:
        logger.info("Pipeline completed (Alpha Vantage quota exhausted, resumes tomorrow) — not a failure.")
    else:
        logger.info("Pipeline completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
