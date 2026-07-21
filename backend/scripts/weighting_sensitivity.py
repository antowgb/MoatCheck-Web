"""Ad-hoc analysis: robustness of the composite weighting (0.6/0.4).

Three volets:
  1. Ranking sensitivity (current stored sub-scores, 23 equities).
  2. Comparative backtest with a temporal validation/test split (8 tickers
     that have point-in-time fundamentals).
  3. Marginal predictive power of each sub-score vs forward returns.

Reuses the production scoring/backtest building blocks; does NOT mutate the DB.
"""

from datetime import date

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

import app.scoring.composite as comp
from app.backtest.engine import _load_closes, _select_basket_at, _series_metrics
from app.data.supabase_client import execute_with_retry, get_supabase
from app.scoring.fundamentals import fundamental_score_at
from app.scoring.risk import (
    annualized_volatility,
    max_drawdown,
    risk_score,
    sharpe_ratio,
    sortino_ratio,
)

WEIGHTS = [(0.5, 0.5), (0.6, 0.4), (0.7, 0.3), (0.8, 0.2), (0.4, 0.6)]
BASELINE = (0.6, 0.4)
sb = get_supabase()


def sep(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


# ---------------------------------------------------------------------------
# Load universe
# ---------------------------------------------------------------------------
stock_rows = execute_with_retry(
    sb.table("stocks").select("ticker,is_benchmark,asset_type,sector")
).data
equities = sorted(
    r["ticker"] for r in stock_rows if not r["is_benchmark"] and r["asset_type"] == "equity"
)
sector_of = {r["ticker"]: r.get("sector") for r in stock_rows}

# Latest stored sub-scores per ticker
score_rows = execute_with_retry(
    sb.table("scores")
    .select("ticker,computed_at,fundamental_score,risk_score")
    .order("computed_at", desc=True)
).data
latest = {}
for r in score_rows:
    latest.setdefault(r["ticker"], r)
scored_now = {
    t: (latest[t]["fundamental_score"], latest[t]["risk_score"])
    for t in equities
    if latest.get(t) and latest[t]["fundamental_score"] is not None
    and latest[t]["risk_score"] is not None
}

sep("SAMPLE INVENTORY")
print(f"Equities in universe:            {len(equities)}")
print(f"  with valid current sub-scores: {len(scored_now)}  -> used in VOLET 1")
print(f"  excluded (null f/r score):     {sorted(set(equities)-set(scored_now))}")

# ---------------------------------------------------------------------------
# VOLET 1 - RANKING SENSITIVITY
# ---------------------------------------------------------------------------
sep("VOLET 1 - RANKING SENSITIVITY (current stored sub-scores)")


def composite(f, r, wf, wr):
    return wf * f + wr * r


rankings = {}
for wf, wr in WEIGHTS:
    ranked = sorted(
        scored_now.items(), key=lambda kv: composite(kv[1][0], kv[1][1], wf, wr), reverse=True
    )
    rankings[(wf, wr)] = [t for t, _ in ranked]

TOPN = 10
base_top = rankings[BASELINE][:TOPN]
print(f"Top-{TOPN} at baseline {BASELINE}:")
for i, t in enumerate(base_top, 1):
    f, r = scored_now[t]
    print(f"  {i:2d}. {t:5s} f={f:6.2f} r={r:6.2f} comp={composite(f,r,*BASELINE):6.2f}")

print(f"\nTop-{TOPN} overlap & full-ranking correlation vs baseline {BASELINE}:")
from scipy.stats import kendalltau, spearmanr

base_full = rankings[BASELINE]
for w in WEIGHTS:
    top = rankings[w][:TOPN]
    common = set(top) & set(base_top)
    # rank vectors over common universe for correlation
    order_w = {t: i for i, t in enumerate(rankings[w])}
    order_b = {t: i for i, t in enumerate(base_full)}
    tickers = list(scored_now)
    rho, _ = spearmanr([order_b[t] for t in tickers], [order_w[t] for t in tickers])
    tau, _ = kendalltau([order_b[t] for t in tickers], [order_w[t] for t in tickers])
    tag = " (baseline)" if w == BASELINE else ""
    print(
        f"  {w}: top-{TOPN} common={len(common)}/{TOPN}  "
        f"entrants={sorted(set(top)-set(base_top))}  "
        f"dropped={sorted(set(base_top)-set(top))}  spearman={rho:.3f} kendall={tau:.3f}{tag}"
    )

# also: does the #1 change?
print("\n#1 ranked ticker per weighting:")
for w in WEIGHTS:
    print(f"  {w}: {rankings[w][0]}")

# ---------------------------------------------------------------------------
# Backtest window helper (reuses point-in-time selection + price data)
# ---------------------------------------------------------------------------
closes_full = {t: _load_closes(t) for t in equities + ["SPY"]}


def scorable_count_at(as_of):
    with_weight_patch(0.6, 0.4)
    sel, scored, _ = _select_basket_at(equities, closes_full, as_of, 999, exact=True)
    return len(scored), scored


def with_weight_patch(wf, wr):
    comp.WEIGHT_FUNDAMENTALS = wf
    comp.WEIGHT_RISK = wr


def window_metrics(basket, start_ts, end_ts):
    """Equal-weighted normalized basket curve over [start_ts, end_ts]."""
    cols = {}
    for t in basket:
        s = closes_full[t]
        s = s[(s.index >= start_ts) & (s.index <= end_ts)]
        if len(s) >= 2:
            cols[t] = s / s.iloc[0]
    if not cols:
        return {"total_return": None, "sharpe": None}, 0
    curve = pd.DataFrame(cols).ffill().mean(axis=1).dropna()
    return _series_metrics(curve), len(cols)


def bench_metrics(start_ts, end_ts):
    s = closes_full["SPY"]
    s = s[(s.index >= start_ts) & (s.index <= end_ts)]
    return _series_metrics(s)


# ---------------------------------------------------------------------------
# VOLET 2 - TEMPORAL SPLIT BACKTEST
# ---------------------------------------------------------------------------
sep("VOLET 2 - COMPARATIVE BACKTEST WITH TEMPORAL SPLIT (anti-overfitting)")

# Usable backtest span = dates where fundamentals are known for the 8 tickers.
# All 8 fundamental tickers have a known snapshot by 2021-11-29; price data
# runs to 2026-07-16. Split that span 60/40.
FUND_START = pd.Timestamp("2022-01-01")  # all 8 snapshots known by now
DATA_END = pd.Timestamp("2026-07-16")
span_days = (DATA_END - FUND_START).days
split_ts = FUND_START + pd.Timedelta(days=int(span_days * 0.6))
print(f"Usable span: {FUND_START.date()} -> {DATA_END.date()} ({span_days} days)")
print(f"Split (60/40) at: {split_ts.date()}")
print(f"  VALIDATION window: {FUND_START.date()} -> {split_ts.date()}")
print(f"  TEST window:       {split_ts.date()} -> {DATA_END.date()}")

n_val, scored_val = scorable_count_at(FUND_START.date())
n_test, scored_test = scorable_count_at(split_ts.date())
print(f"\nScorable tickers (point-in-time fundamentals known):")
print(f"  at validation start {FUND_START.date()}: {n_val}  -> {sorted(t for t,_ in scored_val)}")
print(f"  at test start       {split_ts.date()}: {n_test}  -> {sorted(t for t,_ in scored_test)}")

# With so few scorable tickers, a top-N only differentiates weights if N <
# scorable count. Test several N.
for TOPN_BT in (3, 5):
    print(f"\n--- top-N = {TOPN_BT} ---")
    print("VALIDATION: select basket at window start per weighting, hold to window end")
    val_perf = {}
    for wf, wr in WEIGHTS:
        with_weight_patch(wf, wr)
        sel, scored, _ = _select_basket_at(
            equities, closes_full, FUND_START.date(), TOPN_BT, exact=False
        )
        m, nheld = window_metrics(sel, FUND_START, split_ts)
        val_perf[(wf, wr)] = (sel, m)
        print(
            f"  {(wf,wr)}: basket={sel} ret={m['total_return']} sharpe={m['sharpe']}"
        )
    bm = bench_metrics(FUND_START, split_ts)
    print(f"  SPY: ret={bm['total_return']} sharpe={bm['sharpe']}")

    # pick winner by Sharpe (fallback total_return)
    def key(kv):
        m = kv[1][1]
        return (m["sharpe"] if m["sharpe"] is not None else -9,
                m["total_return"] if m["total_return"] is not None else -9)
    winner = max(val_perf.items(), key=key)
    wwf, wwr = winner[0]
    print(f"  => VALIDATION winner (by Sharpe): {winner[0]}")

    print(f"\nTEST: winner {winner[0]} vs ALL weightings on unseen window")
    test_perf = {}
    for wf, wr in WEIGHTS:
        with_weight_patch(wf, wr)
        sel, scored, _ = _select_basket_at(
            equities, closes_full, split_ts.date(), TOPN_BT, exact=False
        )
        m, nheld = window_metrics(sel, split_ts, DATA_END)
        test_perf[(wf, wr)] = (sel, m)
        mark = " <-- validation winner" if (wf, wr) == (wwf, wwr) else ""
        print(f"  {(wf,wr)}: basket={sel} ret={m['total_return']} sharpe={m['sharpe']}{mark}")
    tbm = bench_metrics(split_ts, DATA_END)
    print(f"  SPY: ret={tbm['total_return']} sharpe={tbm['sharpe']}")
    test_winner = max(test_perf.items(), key=key)
    print(f"  => TEST winner (by Sharpe): {test_winner[0]}")
    print(
        f"  => Validation winner still best on test? "
        f"{'YES' if test_winner[0]==(wwf,wwr) else 'NO -> validation edge was likely noise'}"
    )

with_weight_patch(0.6, 0.4)  # restore

# ---------------------------------------------------------------------------
# VOLET 3 - MARGINAL PREDICTIVE POWER OF SUB-SCORES
# ---------------------------------------------------------------------------
sep("VOLET 3 - MARGINAL PREDICTIVE POWER (sub-score vs forward return)")

AS_OF = split_ts  # score at the split point, measure forward return to DATA_END
print(f"As-of scoring date: {AS_OF.date()};  forward return window -> {DATA_END.date()}")


def forward_return(t):
    s = closes_full[t]
    s = s[(s.index >= AS_OF) & (s.index <= DATA_END)]
    if len(s) < 2:
        return None
    return float(s.iloc[-1] / s.iloc[0] - 1.0)


# risk_score isolated: available for every ticker with enough price history
risk_rows = []
fund_rows = []
fund_snaps = {}
off = 0
while True:
    p = execute_with_retry(sb.table("fundamentals").select("*").range(off, off + 999)).data
    for r in p:
        fund_snaps.setdefault(r["ticker"], []).append(r)
    if len(p) < 1000:
        break
    off += 1000

for t in equities:
    before = closes_full[t][closes_full[t].index < AS_OF]
    rs, _ = risk_score(
        annualized_volatility(before), sharpe_ratio(before), max_drawdown(before), sortino_ratio(before)
    )
    fr = forward_return(t)
    if rs is not None and fr is not None:
        risk_rows.append((t, rs, fr))
    fscore, _ = fundamental_score_at(fund_snaps.get(t, []), AS_OF.date(), sector=sector_of.get(t))
    if fscore is not None and fr is not None:
        fund_rows.append((t, fscore, fr))

print(f"\nrisk_score vs forward return: n={len(risk_rows)} tickers")
for t, rs, fr in sorted(risk_rows, key=lambda x: -x[1]):
    print(f"  {t:5s} risk={rs:6.2f} fwd_ret={fr:+.3f}")
if len(risk_rows) >= 3:
    rr = spearmanr([x[1] for x in risk_rows], [x[2] for x in risk_rows])
    pr = np.corrcoef([x[1] for x in risk_rows], [x[2] for x in risk_rows])[0, 1]
    print(f"  Spearman={rr.correlation:.3f} (p={rr.pvalue:.3f})  Pearson={pr:.3f}")

print(f"\nfundamental_score vs forward return: n={len(fund_rows)} tickers")
for t, fs, fr in sorted(fund_rows, key=lambda x: -x[1]):
    print(f"  {t:5s} fund={fs:6.2f} fwd_ret={fr:+.3f}")
if len(fund_rows) >= 3:
    fr_ = spearmanr([x[1] for x in fund_rows], [x[2] for x in fund_rows])
    pf = np.corrcoef([x[1] for x in fund_rows], [x[2] for x in fund_rows])[0, 1]
    print(f"  Spearman={fr_.correlation:.3f} (p={fr_.pvalue:.3f})  Pearson={pf:.3f}")

print("\nDONE.")
