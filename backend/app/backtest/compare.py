"""Raw price comparison across tickers — independent of scoring/composite/know_date.

Unlike app/backtest/engine.py (which drives investment-selection backtests),
this only ever reads price_history and normalizes it to a base-1.0 curve, the
same way engine.py's basket_curve does. No ranking, no fundamentals, no
point-in-time scoring: purely a visualization helper.
"""

from datetime import date
from typing import Any

import pandas as pd

from app.backtest.engine import _load_closes, _series_metrics


def run_compare(tickers: list[str], start_date: date) -> dict[str, Any]:
    """For each ticker, normalizes its close price to 1.0 at ``start_date``
    and tracks it to the latest available price. Tickers with fewer than 2
    price points on/after ``start_date`` are excluded (reported, not raised —
    existing tickers with a start_date predating their price history are a
    normal case, not an error).
    """
    start_ts = pd.Timestamp(start_date)
    series: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []

    for ticker in tickers:
        closes = _load_closes(ticker)
        after = closes[closes.index >= start_ts]
        if len(after) < 2:
            excluded.append({"ticker": ticker, "reason": "no price data from start_date onward"})
            continue

        normalized = after / after.iloc[0]
        series.append(
            {
                "ticker": ticker,
                "total_return": _series_metrics(after)["total_return"],
                "curve": [
                    {"date": d.date().isoformat(), "value": round(float(v), 4)} for d, v in normalized.items()
                ],
            }
        )

    return {
        "start_date": start_date.isoformat(),
        "series": series,
        "excluded": excluded,
    }
