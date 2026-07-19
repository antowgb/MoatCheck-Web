"""Cross-checks app.backtest.engine._aligned_return against the JS port in
frontend/lib/alignedReturn.ts using a single shared fixture
(fixtures/aligned_return_case.json), so the two implementations can't
silently diverge. Run with pytest if available, else directly:
    python3 tests/test_aligned_return_fixture.py
"""

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.engine import _aligned_return

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "aligned_return_case.json"


def test_aligned_return_matches_fixture() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text())

    stock_index = pd.DatetimeIndex(pd.to_datetime(fixture["stock_dates"]))
    bench = fixture["benchmark_prices"]
    reference_closes = pd.Series(
        [p["close"] for p in bench],
        index=pd.to_datetime([p["date"] for p in bench]),
    ).sort_index()

    result = _aligned_return(reference_closes, stock_index)
    assert result == fixture["expected_aligned_return"], (
        f"_aligned_return returned {result}, fixture expects "
        f"{fixture['expected_aligned_return']} — check it still matches "
        f"the JS port in frontend/lib/alignedReturn.ts"
    )


if __name__ == "__main__":
    test_aligned_return_matches_fixture()
    print("OK: _aligned_return matches fixtures/aligned_return_case.json")
