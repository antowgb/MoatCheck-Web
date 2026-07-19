"use client";

import { Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import Card from "@/components/Card";
import CorrelationHeatmap from "@/components/CorrelationHeatmap";
import Disclaimer from "@/components/Disclaimer";
import EfficientFrontierChart from "@/components/EfficientFrontierChart";
import ExportCsvButton from "@/components/ExportCsvButton";
import HeaderTooltip from "@/components/HeaderTooltip";
import PageHeader from "@/components/PageHeader";
import ScoreBadge from "@/components/ScoreBadge";
import Skeleton from "@/components/Skeleton";
import TickerCombobox, { type TickerComboboxOption } from "@/components/TickerCombobox";
import { api, backendErrorMessage, type CorrelationResult, type EfficientFrontierResult, type ScreenerRow } from "@/lib/api";

const STORAGE_KEY = "moatcheck_portfolio";

interface StoredPortfolio {
  tickers: string[];
  weights: Record<string, number>;
}

function loadPortfolio(): StoredPortfolio {
  if (typeof window === "undefined") return { tickers: [], weights: {} };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { tickers: [], weights: {} };
    const parsed = JSON.parse(raw) as StoredPortfolio;
    return {
      tickers: Array.isArray(parsed.tickers) ? parsed.tickers : [],
      weights: parsed.weights && typeof parsed.weights === "object" ? parsed.weights : {},
    };
  } catch {
    return { tickers: [], weights: {} };
  }
}

function savePortfolio(tickers: string[], weights: Record<string, number>) {
  const stored: StoredPortfolio = { tickers, weights };
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
}

export default function PortfolioPage() {
  const [tickers, setTickers] = useState<string[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const [tickerOptions, setTickerOptions] = useState<TickerComboboxOption[]>([]);
  const [tickerOptionsLoading, setTickerOptionsLoading] = useState(true);

  const [scoreRows, setScoreRows] = useState<ScreenerRow[]>([]);
  const [scoresLoading, setScoresLoading] = useState(false);

  const [correlation, setCorrelation] = useState<CorrelationResult | null>(null);
  const [correlationError, setCorrelationError] = useState<string | null>(null);
  const [correlationLoading, setCorrelationLoading] = useState(false);

  const [frontier, setFrontier] = useState<EfficientFrontierResult | null>(null);
  const [frontierError, setFrontierError] = useState<string | null>(null);
  const [frontierLoading, setFrontierLoading] = useState(false);

  const [weights, setWeights] = useState<Record<string, number>>({});
  const [weightsError, setWeightsError] = useState<string | null>(null);
  const [weightsLoading, setWeightsLoading] = useState(false);

  // Stable key for "the set of tickers", independent of order and of array
  // identity — used as an effect dependency so swapping one ticker for
  // another (same count) still triggers a recompute, unlike `tickers.length`.
  const tickersKey = useMemo(() => [...tickers].sort().join(","), [tickers]);

  // Load from localStorage once on mount (client-only: SSR/static export has no window).
  useEffect(() => {
    const stored = loadPortfolio();
    setTickers(stored.tickers);
    setWeights(stored.weights);
    setHydrated(true);
  }, []);

  // Persist on every change, but only after the initial load — otherwise the
  // empty initial state would overwrite whatever was already saved.
  useEffect(() => {
    if (!hydrated) return;
    savePortfolio(tickers, weights);
  }, [tickers, weights, hydrated]);

  useEffect(() => {
    api
      .listStocks()
      .then((stocks) =>
        setTickerOptions(
          stocks.map((s) => ({ ticker: s.ticker, name: s.name, sector: s.sector })).sort((a, b) => a.ticker.localeCompare(b.ticker))
        )
      )
      .catch(() => setTickerOptions([]))
      .finally(() => setTickerOptionsLoading(false));
  }, []);

  // Scores: the screener endpoint already returns composite/fundamental/risk
  // score for every active equity in one call — reused here instead of one
  // stockDetail() request per portfolio ticker.
  useEffect(() => {
    if (tickers.length === 0) {
      setScoreRows([]);
      return;
    }
    let cancelled = false;
    setScoresLoading(true);
    api
      .screener({})
      .then((res) => {
        if (!cancelled) setScoreRows(res.rows);
      })
      .catch(() => {
        if (!cancelled) setScoreRows([]);
      })
      .finally(() => {
        if (!cancelled) setScoresLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickersKey]);

  // Correlation, weights, and the efficient frontier all recompute
  // automatically whenever the ticker set changes (no button to press) —
  // each guards against a stale response overwriting a newer one if the
  // selection changes again before the request completes.
  useEffect(() => {
    if (tickers.length < 2) {
      setCorrelation(null);
      setCorrelationError(null);
      return;
    }
    let cancelled = false;
    setCorrelationLoading(true);
    setCorrelationError(null);
    api
      .correlation(tickers)
      .then((res) => {
        if (!cancelled) setCorrelation(res);
      })
      .catch((e) => {
        if (!cancelled) {
          setCorrelation(null);
          setCorrelationError(backendErrorMessage(e));
        }
      })
      .finally(() => {
        if (!cancelled) setCorrelationLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickersKey]);

  useEffect(() => {
    if (tickers.length < 2) {
      setWeights({});
      setWeightsError(null);
      return;
    }
    let cancelled = false;
    setWeightsLoading(true);
    setWeightsError(null);
    api
      .portfolioWeights(tickers)
      .then((res) => {
        if (!cancelled) setWeights(res.weights);
      })
      .catch((e) => {
        if (!cancelled) {
          setWeights({});
          setWeightsError(backendErrorMessage(e));
        }
      })
      .finally(() => {
        if (!cancelled) setWeightsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickersKey]);

  useEffect(() => {
    if (tickers.length < 2) {
      setFrontier(null);
      setFrontierError(null);
      return;
    }
    let cancelled = false;
    setFrontierLoading(true);
    setFrontierError(null);
    api
      .efficientFrontier(tickers)
      .then((res) => {
        if (!cancelled) setFrontier(res);
      })
      .catch((e) => {
        if (!cancelled) {
          setFrontier(null);
          setFrontierError(backendErrorMessage(e));
        }
      })
      .finally(() => {
        if (!cancelled) setFrontierLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickersKey]);

  const scoreByTicker = useMemo(() => {
    const m = new Map<string, ScreenerRow>();
    for (const r of scoreRows) m.set(r.ticker, r);
    return m;
  }, [scoreRows]);

  const toggleTicker = (ticker: string) => {
    setTickers((prev) => (prev.includes(ticker) ? prev.filter((t) => t !== ticker) : [...prev, ticker]));
  };

  const handleClearPortfolio = () => {
    if (tickers.length === 0) return;
    if (!window.confirm(`Remove all ${tickers.length} tickers from your portfolio?`)) return;
    setTickers([]);
  };

  // Off-diagonal summary: overall average pairwise correlation, and the
  // ticker whose own average correlation to the rest is highest (the one
  // contributing least to diversification).
  const diversitySummary = useMemo(() => {
    if (!correlation) return null;
    const { tickers: t, matrix } = correlation;
    const n = t.length;
    if (n < 2) return null;

    let sum = 0;
    let count = 0;
    let worstTicker = t[0];
    let worstAvg = -Infinity;
    for (let i = 0; i < n; i++) {
      let rowSum = 0;
      let rowCount = 0;
      for (let j = 0; j < n; j++) {
        if (i === j) continue;
        sum += matrix[i][j];
        count += 1;
        rowSum += matrix[i][j];
        rowCount += 1;
      }
      const rowAvg = rowCount > 0 ? rowSum / rowCount : 0;
      if (rowAvg > worstAvg) {
        worstAvg = rowAvg;
        worstTicker = t[i];
      }
    }
    return { average: count > 0 ? sum / count : null, mostCorrelatedTicker: worstTicker, mostCorrelatedAvg: worstAvg };
  }, [correlation]);

  const scoresCsvRows = useMemo(
    () =>
      tickers.map((t) => {
        const row = scoreByTicker.get(t);
        return {
          ticker: t,
          composite_score: row?.composite_score ?? null,
          fundamental_score: row?.fundamental_score ?? null,
          risk_score: row?.risk_score ?? null,
          weight: weights[t] ?? null,
        };
      }),
    [tickers, scoreByTicker, weights]
  );

  const correlationCsvRows = useMemo(() => {
    if (!correlation) return [];
    return correlation.tickers.map((rowTicker, i) => {
      const row: Record<string, string | number> = { ticker: rowTicker };
      correlation.tickers.forEach((colTicker, j) => {
        row[colTicker] = correlation.matrix[i][j];
      });
      return row;
    });
  }, [correlation]);

  const frontierCsvRows = useMemo(() => {
    if (!frontier) return [];
    return frontier.frontier.map((p) => ({
      target_return: p.target_return,
      volatility: p.volatility,
      ...p.weights,
    }));
  }, [frontier]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Portfolio"
        subtitle="Track a set of tickers, see their scores at a glance, and check how correlated they are to each other. Saved locally in your browser only — nothing is sent to the server besides the score/correlation/weight/frontier lookups."
      />
      <Disclaimer />

      <Card>
        <TickerCombobox
          label="Portfolio tickers"
          options={tickerOptions}
          selected={tickers}
          onToggle={toggleTicker}
          onClear={handleClearPortfolio}
          emptyLabel="No ticker tracked yet."
          loading={tickerOptionsLoading}
        />
      </Card>

      {tickers.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Your portfolio is empty. Add tickers above to see their scores and correlation.
          </p>
        </Card>
      ) : (
        <>
          <div className="flex justify-end">
            <button
              onClick={handleClearPortfolio}
              className="inline-flex items-center gap-1.5 text-rose-600 dark:text-rose-400 px-3 py-1.5 rounded-lg text-sm hover:bg-rose-50 dark:hover:bg-rose-500/10 transition-colors"
            >
              <Trash2 size={13} />
              Clear portfolio
            </button>
          </div>

          <Card title="Scores">
            {tickers.length < 2 && (
              <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">
                Add at least 2 tickers to compute position weights.
              </p>
            )}
            {weightsError && <p className="text-red-600 dark:text-rose-400 text-sm mb-4">Error: {weightsError}</p>}

            {scoresLoading ? (
              <Skeleton className="h-40" />
            ) : (
              <>
                <div className="flex justify-end mb-2">
                  <ExportCsvButton
                    data={scoresCsvRows}
                    filename="portfolio_scores"
                    columns={["ticker", "composite_score", "fundamental_score", "risk_score", "weight"]}
                  />
                </div>
                <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
                  <table className="min-w-full divide-y divide-slate-100 dark:divide-slate-800">
                    <thead className="bg-slate-50/80 dark:bg-slate-800/40">
                      <tr className="text-left text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">
                        <th className="px-3 py-2">
                          <HeaderTooltip label="Ticker" tooltip="Stock ticker symbol." />
                        </th>
                        <th className="px-3 py-2">
                          <HeaderTooltip
                            label="Composite"
                            tooltip="Weighted blend of the fundamental (60%) and risk (40%) scores, 0-100."
                          />
                        </th>
                        <th className="px-3 py-2">
                          <HeaderTooltip
                            label="Fundamental"
                            tooltip="Revenue growth, margins, ROE, leverage, and free cash flow, blended into a 0-100 score."
                          />
                        </th>
                        <th className="px-3 py-2">
                          <HeaderTooltip
                            label="Risk"
                            tooltip="Volatility, Sharpe, Sortino, and max drawdown, blended into a 0-100 score."
                          />
                        </th>
                        <th className="px-3 py-2">
                          <HeaderTooltip
                            label="Weight"
                            tooltip="Suggested position size, inversely proportional to annualized volatility."
                          />
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {tickers.map((t) => {
                        const row = scoreByTicker.get(t);
                        const weight = weights[t];
                        return (
                          <tr key={t} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
                            <td className="px-3 py-2 font-medium font-mono">{t}</td>
                            <td className="px-3 py-2">
                              <ScoreBadge score={row?.composite_score ?? null} />
                            </td>
                            <td className="px-3 py-2">
                              <ScoreBadge score={row?.fundamental_score ?? null} />
                            </td>
                            <td className="px-3 py-2">
                              <ScoreBadge score={row?.risk_score ?? null} />
                            </td>
                            <td className="px-3 py-2 text-sm font-mono text-slate-700 dark:text-slate-300">
                              {weightsLoading ? (
                                <span className="inline-block h-3 w-10 rounded bg-slate-200 dark:bg-slate-700 animate-pulse" />
                              ) : weight !== undefined ? (
                                `${(weight * 100).toFixed(1)} %`
                              ) : (
                                "N/A"
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            <p className="text-xs text-slate-400 dark:text-slate-600 mt-2">
              ETFs and benchmark tickers have no composite score (not part of the scored investable universe) and show N/A.
              Weights are inverse-volatility position sizes, computed automatically once at least 2 tickers are tracked.
            </p>
          </Card>

          <Card title="Diversity">
            {tickers.length < 2 ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Add at least 2 tickers to compute a correlation matrix.
              </p>
            ) : correlationLoading ? (
              <Skeleton className="h-48" />
            ) : (
              <>
                {correlationError && (
                  <p className="text-red-600 dark:text-rose-400 text-sm mb-4">Error: {correlationError}</p>
                )}

                {correlation && diversitySummary && (
                  <>
                    <div className="flex justify-end mb-2">
                      <ExportCsvButton
                        data={correlationCsvRows}
                        filename="portfolio_correlation"
                        columns={["ticker", ...correlation.tickers]}
                      />
                    </div>
                    <div className="flex flex-wrap gap-4 mb-4 text-sm">
                      <div>
                        <span className="text-slate-500 dark:text-slate-400">Average pairwise correlation: </span>
                        <span className="font-mono font-medium text-slate-900 dark:text-slate-50">
                          {diversitySummary.average?.toFixed(3) ?? "N/A"}
                        </span>
                      </div>
                      <div>
                        <span className="text-slate-500 dark:text-slate-400">Most correlated to the rest: </span>
                        <span className="font-mono font-medium text-slate-900 dark:text-slate-50">
                          {diversitySummary.mostCorrelatedTicker} ({diversitySummary.mostCorrelatedAvg.toFixed(3)})
                        </span>
                      </div>
                    </div>
                    <CorrelationHeatmap tickers={correlation.tickers} matrix={correlation.matrix} />
                  </>
                )}
              </>
            )}
          </Card>

          <Card title="Efficient frontier">
            {tickers.length < 2 ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Add at least 2 tickers to compute an efficient frontier.
              </p>
            ) : frontierLoading ? (
              <Skeleton className="h-80" />
            ) : (
              <>
                {frontierError && (
                  <p className="text-red-600 dark:text-rose-400 text-sm mb-4">Error: {frontierError}</p>
                )}

                {frontier && (
                  <>
                    {frontier.skipped_target_returns.length > 0 && (
                      <p className="text-xs text-amber-700 dark:text-amber-400 mb-3">
                        {frontier.skipped_target_returns.length} point{frontier.skipped_target_returns.length > 1 ? "s" : ""}{" "}
                        did not converge and {frontier.skipped_target_returns.length > 1 ? "were" : "was"} excluded from the
                        curve.
                      </p>
                    )}
                    {frontier.note && (
                      <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">{frontier.note}</p>
                    )}
                    {frontier.excluded.length > 0 && (
                      <p className="text-xs text-slate-400 dark:text-slate-600 mb-3">
                        Excluded from the frontier: {frontier.excluded.map((e) => `${e.ticker} (${e.reason})`).join(", ")}.
                      </p>
                    )}
                    <div className="flex justify-end mb-2">
                      <ExportCsvButton
                        data={frontierCsvRows}
                        filename="portfolio_efficient_frontier"
                        columns={["target_return", "volatility", ...frontier.tickers]}
                      />
                    </div>
                    <EfficientFrontierChart
                      frontier={frontier.frontier.map((p) => ({ volatility: p.volatility, return: p.target_return }))}
                      maxSharpe={{ volatility: frontier.max_sharpe_point.volatility, return: frontier.max_sharpe_point.expected_return }}
                    />
                    <div className="flex flex-wrap gap-4 mt-3 text-sm">
                      <div>
                        <span className="text-slate-500 dark:text-slate-400">Max Sharpe portfolio: </span>
                        <span className="font-mono font-medium text-slate-900 dark:text-slate-50">
                          {Object.entries(frontier.max_sharpe_point.weights)
                            .map(([t, w]) => `${t} ${(w * 100).toFixed(0)}%`)
                            .join(", ")}
                        </span>
                      </div>
                      <div>
                        <span className="text-slate-500 dark:text-slate-400">Sharpe: </span>
                        <span className="font-mono font-medium text-slate-900 dark:text-slate-50">
                          {frontier.max_sharpe_point.sharpe_ratio?.toFixed(2) ?? "N/A"}
                        </span>
                      </div>
                    </div>
                  </>
                )}
              </>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
