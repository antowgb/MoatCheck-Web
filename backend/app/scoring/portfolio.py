"""Portfolio-level position sizing — independent of scoring/composite/know_date.

Like app/backtest/compare.py, this reuses app/backtest/engine.py::_load_closes
for price data and app/scoring/risk.py for the underlying risk metric, rather
than duplicating either.
"""

import logging

import numpy as np
from scipy.optimize import minimize

from app.backtest.engine import _load_closes
from app.scoring.risk import PERIODS_PER_YEAR, RISK_FREE_RATE, annualized_volatility, covariance_matrix, periodic_returns

logger = logging.getLogger(__name__)

# Number of target-return points sampled along the frontier.
FRONTIER_POINTS = 15

# Below this, at least 3 of FRONTIER_POINTS grid points must have converged for
# the result to be considered a meaningful curve rather than a handful of
# scattered, possibly-misleading points.
MIN_CONVERGED_POINTS = 3

_SLSQP_OPTIONS = {"maxiter": 200, "ftol": 1e-10}
# Independent post-hoc check on top of scipy's own `result.success`: the
# objective (portfolio variance, ~0.01-0.25) and the equality constraints
# (sum(w)=1, ~1) are on very different scales, so SLSQP's default tolerance
# can report success while the sum-to-1 constraint is off by more than this.
_CONSTRAINT_TOL = 1e-4


def inverse_volatility_weights(tickers: list[str]) -> dict[str, float]:
    """Position weights inversely proportional to annualized volatility:
    w_i = (1/vol_i) / sum(1/vol_j), normalized to sum to 1.0.

    Tickers with missing or zero volatility are excluded from the
    calculation (logged, not raised) and never appear in the result — the
    weights of the remaining tickers are normalized among themselves, so the
    returned weights always sum to 1.0 (no zero-weight entry left over).
    """
    inv_vol: dict[str, float] = {}
    for ticker in tickers:
        vol = annualized_volatility(_load_closes(ticker))
        if not vol:
            logger.warning("%s: excluded from inverse-volatility weighting (missing or zero volatility).", ticker)
            continue
        inv_vol[ticker] = 1.0 / vol

    total = sum(inv_vol.values())
    if total == 0:
        return {}
    return {ticker: round(w / total, 4) for ticker, w in inv_vol.items()}


def efficient_frontier(tickers: list[str]) -> dict:
    """Long-only Markowitz efficient frontier: a curve of minimum-variance
    portfolios across a grid of target returns, plus the max-Sharpe (tangency)
    portfolio as a distinguished point.

    Tickers with missing or zero volatility are excluded (logged, reported in
    ``excluded``, matching inverse_volatility_weights's convention) — the
    curve and tangency portfolio are computed only over the remaining tickers.

    Returns a dict with either an ``"error"`` key (caller should surface this
    as an explicit failure, never a partial/misleading success) or the full
    result: ``tickers``, ``excluded``, ``frontier``, ``skipped_target_returns``,
    ``max_sharpe_point``, ``note``.
    """
    closes_by_ticker = {t: _load_closes(t) for t in tickers}

    included: list[str] = []
    excluded: list[dict[str, str]] = []
    mu: dict[str, float] = {}
    for t in tickers:
        vol = annualized_volatility(closes_by_ticker[t])
        if not vol:
            logger.warning("%s: excluded from efficient frontier (missing or zero volatility).", t)
            excluded.append({"ticker": t, "reason": "zero or undefined volatility"})
            continue
        included.append(t)
        mu[t] = float(periodic_returns(closes_by_ticker[t]).mean() * PERIODS_PER_YEAR)

    if len(included) < 2:
        return {"error": "Fewer than 2 tickers have usable volatility; an efficient frontier needs at least 2."}

    cov_df = covariance_matrix({t: closes_by_ticker[t] for t in included}).reindex(index=included, columns=included)
    sigma = cov_df.to_numpy()
    mu_vec = np.array([mu[t] for t in included])
    n = len(included)
    bounds = [(0.0, 1.0)] * n
    equal_weights = np.full(n, 1.0 / n)

    def _sum_to_one(w: np.ndarray) -> float:
        return float(np.sum(w) - 1.0)

    def _converged(result, target_return: float | None = None) -> bool:
        if not result.success:
            return False
        if abs(float(np.sum(result.x) - 1.0)) > _CONSTRAINT_TOL:
            return False
        if target_return is not None and abs(float(result.x @ mu_vec) - target_return) > 1e-3:
            return False
        return True

    def _neg_sharpe(w: np.ndarray) -> float:
        ret = float(w @ mu_vec)
        vol = float(np.sqrt(max(w @ sigma @ w, 1e-12)))
        return -((ret - RISK_FREE_RATE) / vol)

    sharpe_result = minimize(
        _neg_sharpe,
        equal_weights,
        method="SLSQP",
        bounds=bounds,
        constraints=[{"type": "eq", "fun": _sum_to_one}],
        options=_SLSQP_OPTIONS,
    )
    if not _converged(sharpe_result):
        return {"error": f"Efficient frontier optimization did not converge: {sharpe_result.message}"}

    w_sharpe = sharpe_result.x
    sharpe_ret = float(w_sharpe @ mu_vec)
    sharpe_vol = float(np.sqrt(w_sharpe @ sigma @ w_sharpe))
    max_sharpe_point = {
        "expected_return": round(sharpe_ret, 4),
        "volatility": round(sharpe_vol, 4),
        "sharpe_ratio": round((sharpe_ret - RISK_FREE_RATE) / sharpe_vol, 4) if sharpe_vol > 0 else None,
        "weights": {t: round(float(w), 4) for t, w in zip(included, w_sharpe)},
    }

    # Global minimum-variance portfolio (no return constraint) anchors the
    # low end of the grid: target returns are only sampled from this return
    # upward, so we never trace the inefficient (dominated) lower branch of
    # the Markowitz parabola.
    min_var_result = minimize(
        lambda w: float(w @ sigma @ w),
        equal_weights,
        method="SLSQP",
        bounds=bounds,
        constraints=[{"type": "eq", "fun": _sum_to_one}],
        options=_SLSQP_OPTIONS,
    )
    if not _converged(min_var_result):
        return {"error": f"Efficient frontier optimization did not converge: {min_var_result.message}"}

    w_min_var = min_var_result.x
    min_var_ret = float(w_min_var @ mu_vec)
    min_var_vol = float(np.sqrt(w_min_var @ sigma @ w_min_var))
    min_variance_point = {
        "expected_return": round(min_var_ret, 4),
        "volatility": round(min_var_vol, 4),
        "weights": {t: round(float(w), 4) for t, w in zip(included, w_min_var)},
    }

    max_ret = float(mu_vec.max())
    note: str | None = None
    if max_ret - min_var_ret < 1e-6:
        target_returns = [min_var_ret]
        note = "Expected returns are nearly identical across tickers; the frontier degenerates to a single point."
    else:
        target_returns = list(np.linspace(min_var_ret, max_ret, FRONTIER_POINTS))

    frontier: list[dict] = []
    skipped_target_returns: list[float] = []
    warm_start = w_min_var
    for target in target_returns:

        def _hits_target(w: np.ndarray, target: float = target) -> float:
            return float(w @ mu_vec - target)

        result = minimize(
            lambda w: float(w @ sigma @ w),
            warm_start,
            method="SLSQP",
            bounds=bounds,
            constraints=[
                {"type": "eq", "fun": _sum_to_one},
                {"type": "eq", "fun": _hits_target},
            ],
            options=_SLSQP_OPTIONS,
        )
        if not _converged(result, target_return=target):
            skipped_target_returns.append(round(float(target), 4))
            continue
        warm_start = result.x
        vol = float(np.sqrt(result.x @ sigma @ result.x))
        frontier.append(
            {
                "target_return": round(float(target), 4),
                "volatility": round(vol, 4),
                "weights": {t: round(float(w), 4) for t, w in zip(included, result.x)},
            }
        )

    if len(frontier) < min(MIN_CONVERGED_POINTS, len(target_returns)):
        return {
            "error": (
                f"Efficient frontier optimization did not converge: only {len(frontier)} of "
                f"{len(target_returns)} target returns produced a usable point."
            )
        }

    # Safety net: even with the grid anchored at the min-variance return,
    # numerical noise near-adjacent SLSQP solves can still leave a point that
    # is strictly dominated (worse or equal return AND worse or equal
    # volatility than another point) — drop those before returning.
    frontier = _drop_dominated(frontier)

    return {
        "tickers": included,
        "excluded": excluded,
        "frontier": frontier,
        "skipped_target_returns": skipped_target_returns,
        "max_sharpe_point": max_sharpe_point,
        "min_variance_point": min_variance_point,
        "note": note,
    }


def _drop_dominated(frontier: list[dict]) -> list[dict]:
    """Drop any point strictly dominated by another: same-or-lower return
    paired with same-or-higher volatility (i.e. another point is at least as
    good on both axes and strictly better on one)."""
    kept = []
    for i, point in enumerate(frontier):
        dominated = False
        for j, other in enumerate(frontier):
            if i == j:
                continue
            better_or_equal_return = other["target_return"] >= point["target_return"]
            better_or_equal_vol = other["volatility"] <= point["volatility"]
            strictly_better = other["target_return"] > point["target_return"] or other["volatility"] < point["volatility"]
            if better_or_equal_return and better_or_equal_vol and strictly_better:
                dominated = True
                break
        if not dominated:
            kept.append(point)
    return kept
