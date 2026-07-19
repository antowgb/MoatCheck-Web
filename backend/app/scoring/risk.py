"""Risk metrics from periodic returns.

Prices stored by ``app/data/fetch.py`` are WEEKLY (Alpha Vantage's 5-year daily
history being premium): annualization is therefore done over 52 periods/year.

All functions take pandas series of closing prices indexed
by date and return None when the data is insufficient.
"""

import numpy as np
import pandas as pd

# Annual risk-free rate used for the Sharpe ratio (approximation of the
# current short-term rate). Adjust here if rates move.
RISK_FREE_RATE = 0.03

# Number of trading periods per year. Prices are WEEKLY (Alpha Vantage
# free tier source) -> 52. Switch back to 252 if we return to daily data.
PERIODS_PER_YEAR = 52

# Backward-compatible alias (the old name now points to the weekly cadence).
TRADING_DAYS_PER_YEAR = PERIODS_PER_YEAR

# Minimum number of price points to compute reliable stats (~6 months weekly)
MIN_PRICE_POINTS = 26


def periodic_returns(closes: pd.Series) -> pd.Series:
    """Period-over-period returns (weekly, per PERIODS_PER_YEAR)."""
    return closes.sort_index().pct_change().dropna()


def annualized_volatility(closes: pd.Series) -> float | None:
    """Standard deviation of periodic returns × √(periods per year)."""
    if len(closes) < MIN_PRICE_POINTS:
        return None
    return float(periodic_returns(closes).std() * np.sqrt(PERIODS_PER_YEAR))


def sharpe_ratio(closes: pd.Series, risk_free_rate: float = RISK_FREE_RATE) -> float | None:
    """(annualized return - risk-free rate) / annualized volatility."""
    if len(closes) < MIN_PRICE_POINTS:
        return None
    returns = periodic_returns(closes)
    vol = float(returns.std() * np.sqrt(PERIODS_PER_YEAR))
    if vol == 0:
        return None
    annual_return = float(returns.mean() * PERIODS_PER_YEAR)
    return (annual_return - risk_free_rate) / vol


def sortino_ratio(closes: pd.Series, risk_free_rate: float = RISK_FREE_RATE) -> float | None:
    """(annualized return - risk-free rate) / annualized downside deviation.

    Downside deviation uses only negative periodic returns (not all returns).
    """
    if len(closes) < MIN_PRICE_POINTS:
        return None
    returns = periodic_returns(closes)
    downside = returns[returns < 0]
    if downside.empty:
        return None
    downside_dev = float(downside.std() * np.sqrt(PERIODS_PER_YEAR))
    if downside_dev == 0:
        return None
    annual_return = float(returns.mean() * PERIODS_PER_YEAR)
    return (annual_return - risk_free_rate) / downside_dev


def max_drawdown(closes: pd.Series) -> float | None:
    """Maximum drawdown over the period (negative value, e.g. -0.35)."""
    if len(closes) < 2:
        return None
    closes = closes.sort_index()
    running_max = closes.cummax()
    drawdowns = closes / running_max - 1.0
    return float(drawdowns.min())


def returns_frame(closes_by_ticker: dict[str, pd.Series]) -> pd.DataFrame:
    """Periodic returns per ticker, aligned to their common dates.

    Takes {ticker: closing price series}. Shared by correlation_matrix and
    covariance_matrix so both are computed over the exact same aligned
    sample — mixing a correlation computed over one window with a volatility
    computed over another (e.g. a longer, unaligned history) would not be a
    real covariance matrix.
    """
    return pd.DataFrame({t: periodic_returns(closes) for t, closes in closes_by_ticker.items()})


def correlation_matrix(closes_by_ticker: dict[str, pd.Series]) -> pd.DataFrame:
    """Correlation matrix of periodic (weekly) returns across tickers.

    Takes {ticker: closing price series}, aligns common dates.
    """
    return returns_frame(closes_by_ticker).corr()


def covariance_matrix(closes_by_ticker: dict[str, pd.Series]) -> pd.DataFrame:
    """Annualized covariance matrix of periodic (weekly) returns across tickers.

    Built from the same date-aligned returns as correlation_matrix (see
    returns_frame), not from per-ticker volatility computed independently.
    """
    return returns_frame(closes_by_ticker).cov() * PERIODS_PER_YEAR


def risk_score(
    volatility: float | None,
    sharpe: float | None,
    mdd: float | None,
    sortino: float | None = None,
) -> tuple[float | None, dict]:
    """Risk sub-score 0-100 (higher = more favorable risk profile).

    Simple, bounded linear normalizations:
    - volatility: 100 at <=15% annualized, 0 at >=60%
    - sharpe    : 0 at <=0, 100 at >=2
    - drawdown  : 100 at >=-10%, 0 at <=-70%
    Missing components are excluded (not set to 0) and flagged
    in the breakdown.

    ``sortino`` is exposed in the breakdown for visibility only; it is not
    folded into ``components``/the averaged score (not yet weighted in).
    """
    components: dict[str, float] = {}
    missing: list[str] = []

    if volatility is not None:
        components["volatility"] = _clamp01((0.60 - volatility) / (0.60 - 0.15)) * 100
    else:
        missing.append("volatility")
    if sharpe is not None:
        components["sharpe"] = _clamp01(sharpe / 2.0) * 100
    else:
        missing.append("sharpe")
    if mdd is not None:
        components["max_drawdown"] = _clamp01((mdd + 0.70) / (0.70 - 0.10)) * 100
    else:
        missing.append("max_drawdown")

    breakdown = {"components": components, "missing": missing, "sortino": sortino}
    if not components:
        return None, breakdown
    score = sum(components.values()) / len(components)
    return round(score, 2), breakdown


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))
