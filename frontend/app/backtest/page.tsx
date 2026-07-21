"use client";

import { motion } from "framer-motion";
import { Play } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import Card from "@/components/Card";
import ExportCsvButton from "@/components/ExportCsvButton";
import HeaderTooltip from "@/components/HeaderTooltip";
import PageHeader from "@/components/PageHeader";
import PerStockBenchmarkRow from "@/components/PerStockBenchmarkRow";
import PriceChart from "@/components/PriceChart";
import Skeleton from "@/components/Skeleton";
import TickerCombobox from "@/components/TickerCombobox";
import { api, type BacktestResult, type BenchmarkOption, type Stock } from "@/lib/api";

const inputCls =
  "border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-400 dark:focus:border-emerald-500 transition-colors";

// Short display name for the general (is_benchmark=true) benchmarks — there's
// only one in the DB today (SPY); add here if more are ever introduced.
const GENERAL_BENCHMARK_SHORT_NAME: Record<string, string> = {
  SPY: "S&P 500",
};

function benchmarkOptionLabel(b: BenchmarkOption): string {
  const shortName = b.is_benchmark ? GENERAL_BENCHMARK_SHORT_NAME[b.ticker] ?? b.name : b.sector_name;
  return shortName ? `${b.ticker} - ${shortName}` : b.ticker;
}

export default function BacktestPage() {
  const [startDate, setStartDate] = useState("2023-07-01");
  const [topN, setTopN] = useState(5);
  const [benchmark, setBenchmark] = useState("SPY");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [stocksLoading, setStocksLoading] = useState(true);
  const [benchmarkOptions, setBenchmarkOptions] = useState<BenchmarkOption[]>([]);
  const [universe, setUniverse] = useState<string[]>([]);

  useEffect(() => {
    api
      .listStocks()
      .then(setStocks)
      .catch(() => setStocks([]))
      .finally(() => setStocksLoading(false));
    api.listBenchmarks().then(setBenchmarkOptions).catch(() => setBenchmarkOptions([]));
  }, []);

  const toggleUniverseTicker = (ticker: string) => {
    setUniverse((u) => (u.includes(ticker) ? u.filter((t) => t !== ticker) : [...u, ticker]));
  };

  const manualMode = universe.length > 0;

  const run = () => {
    setLoading(true);
    setError(null);
    setResult(null);
    api
      .backtest({
        start_date: startDate,
        top_n: manualMode ? undefined : topN,
        benchmark,
        tickers: manualMode ? universe : undefined,
      })
      .then(setResult)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };

  const chartData = useMemo(() => {
    if (!result?.basket_curve) return [];
    const byDate = new Map<string, Record<string, string | number>>();
    for (const p of result.basket_curve) byDate.set(p.date, { date: p.date, basket: p.value });
    for (const p of result.benchmark_curve ?? []) {
      const row = byDate.get(p.date) ?? { date: p.date };
      row.benchmark = p.value;
      byDate.set(p.date, row);
    }
    return Array.from(byDate.values()).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  }, [result]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Backtest"
        subtitle="Simulate picking the top-N tickers by composite score at a past date, using only data known at that time, and compare the resulting basket to a benchmark since then."
      />
      <Card>
        <div className="flex flex-wrap gap-3 items-end">
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Start date</span>
            <input
              type="date"
              className={inputCls}
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </label>
          {!manualMode && (
            <label className="text-sm">
              <span className="block text-slate-500 dark:text-slate-400 mb-1">Top N</span>
              <input
                type="number"
                min={1}
                max={50}
                className={`${inputCls} w-20`}
                value={topN}
                onChange={(e) => setTopN(Number(e.target.value))}
              />
            </label>
          )}
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Benchmark</span>
            <select
              className={`${inputCls} w-40`}
              value={benchmark}
              onChange={(e) => setBenchmark(e.target.value)}
            >
              {benchmarkOptions.length === 0 && <option value={benchmark}>{benchmark}</option>}
              {benchmarkOptions.map((b) => (
                <option key={b.ticker} value={b.ticker}>
                  {benchmarkOptionLabel(b)}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={run}
            disabled={loading}
            className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50 transition-colors"
          >
            <Play size={13} />
            {loading ? "Computing…" : "Run"}
          </button>
        </div>
      </Card>

      <Card>
        <TickerCombobox
          label="Limit universe to specific tickers (optional)"
          // ETFs are never scorable (recompute_scores excludes them structurally),
          // so they never belong in an investable-universe selector.
          options={stocks.filter((s) => s.asset_type === "equity")}
          selected={universe}
          onToggle={toggleUniverseTicker}
          onClear={() => setUniverse([])}
          loading={stocksLoading}
        />
        <p className="text-xs text-slate-400 dark:text-slate-600 mt-2">
          {manualMode
            ? "Backtesting the exact tickers selected above: no ranking or Top N applied."
            : "Backtesting the top N stocks by composite score. Leave empty to use the whole tracked universe (default behavior)."}
        </p>
      </Card>

      {error && <p className="text-red-600 dark:text-rose-400 text-sm">Error: {error}</p>}
      {loading && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      )}
      {result?.error && <p className="text-red-600 dark:text-rose-400 text-sm">{result.error}</p>}
      {result?.tickers_excluded && result.tickers_excluded.length > 0 && (
        <Card title="Excluded tickers">
          <ul className="text-sm space-y-1">
            {result.tickers_excluded.map((e) => (
              <li key={e.ticker} className="flex gap-3">
                <span className="font-medium font-mono">{e.ticker}</span>
                <span className="text-slate-500 dark:text-slate-400">{e.reason}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result && !result.error && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25 }}
          className="space-y-6"
        >
          {result.note && (
            <p className="text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 rounded-xl p-3 text-sm">
              {result.note}
            </p>
          )}
          {result.benchmark_data_unavailable && (
            <p className="text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 rounded-xl p-3 text-sm">
              No price data available for benchmark {result.benchmark?.ticker}: the benchmark metrics and curve
              below are empty.
            </p>
          )}
          {result.low_sample_warning && result.low_sample_warning_message && (
            <p className="text-orange-800 dark:text-orange-300 bg-orange-50 dark:bg-orange-500/10 border border-orange-200 dark:border-orange-500/20 rounded-xl p-3 text-sm">
              {result.low_sample_warning_message}
            </p>
          )}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Card title="Selected basket">
              {result.selected_tickers && result.scores_at_start ? (
                <ul className="text-sm space-y-0.5">
                  {result.selected_tickers.map((t) => (
                    <li key={t} className="flex justify-between gap-3 font-mono">
                      <span className="text-slate-900 dark:text-slate-100">{t}</span>
                      <span className="text-slate-500 dark:text-slate-400">
                        {result.scores_at_start?.[t]?.toFixed(1) ?? "N/A"}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm">{result.selected_tickers?.join(", ")}</p>
              )}
              {result.tickers_scorable_count != null && (
                <p className="text-xs text-slate-400 dark:text-slate-600 mt-2">
                  {result.tickers_scorable_count} ticker(s) scorable at this date
                  {result.tickers_excluded_count ? `, ${result.tickers_excluded_count} excluded` : ""}
                </p>
              )}
            </Card>
            <Card title="Total return">
              <p className="text-sm font-mono">
                Basket:{" "}
                <b className="text-slate-900 dark:text-slate-50">
                  {result.basket?.total_return != null ? `${(result.basket.total_return * 100).toFixed(1)} %` : "N/A"}
                </b>
                <br />
                {result.benchmark?.ticker}:{" "}
                <b className="text-slate-900 dark:text-slate-50">
                  {result.benchmark?.total_return != null
                    ? `${(result.benchmark.total_return * 100).toFixed(1)} %`
                    : "N/A"}
                </b>
              </p>
            </Card>
            <Card title="Sharpe ratio">
              <p className="text-sm font-mono">
                Basket: <b className="text-slate-900 dark:text-slate-50">{result.basket?.sharpe ?? "N/A"}</b>
                <br />
                {result.benchmark?.ticker}:{" "}
                <b className="text-slate-900 dark:text-slate-50">{result.benchmark?.sharpe ?? "N/A"}</b>
              </p>
            </Card>
          </div>
          {result.per_stock_vs_benchmarks && result.per_stock_vs_benchmarks.length > 0 && (
            <Card title="Per-stock comparison">
              <div className="flex justify-end mb-2">
                <ExportCsvButton
                  data={result.per_stock_vs_benchmarks}
                  filename="backtest_per_stock"
                  columns={[
                    "ticker",
                    "stock_return",
                    "benchmark_ticker",
                    "benchmark_return",
                    "vs_benchmark",
                    "sector_benchmark_ticker",
                    "sector_return",
                    "vs_sector_benchmark",
                  ]}
                />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-slate-500 dark:text-slate-400">
                      <th className="py-2 pr-3 font-medium">
                        <HeaderTooltip label="Ticker" tooltip="Stock ticker symbol." />
                      </th>
                      <th className="py-2 pr-3 font-medium">
                        <HeaderTooltip
                          label="Return"
                          tooltip="Total price return over the backtest period, aligned to this stock's own available price history."
                        />
                      </th>
                      <th className="py-2 pr-3 font-medium">
                        <HeaderTooltip
                          label={result.benchmark?.ticker ?? "Benchmark"}
                          tooltip="Total return of the overall market benchmark (e.g. SPY) over the same aligned period."
                        />
                      </th>
                      <th className="py-2 pr-3 font-medium">
                        <HeaderTooltip
                          label="vs benchmark"
                          tooltip="This stock's return minus the market benchmark's return over the same period."
                        />
                      </th>
                      <th className="py-2 pr-3 font-medium">
                        <HeaderTooltip
                          label="Sector ETF"
                          tooltip="The sector-tracking ETF assigned to this stock (e.g. XLK for Technology)."
                        />
                      </th>
                      <th className="py-2 pr-3 font-medium">
                        <HeaderTooltip
                          label="Sector return"
                          tooltip="Total return of this stock's sector ETF over the same aligned period."
                        />
                      </th>
                      <th className="py-2 pr-3 font-medium">
                        <HeaderTooltip
                          label="vs sector"
                          tooltip="This stock's return minus its sector ETF's return over the same period."
                        />
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.per_stock_vs_benchmarks.map((row) => (
                      <PerStockBenchmarkRow
                        key={row.ticker}
                        row={row}
                        startDate={result.start_date ?? startDate}
                        stocks={stocks}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
          <Card title="Curve (base 1.0)">
            <div className="flex justify-end mb-2">
              <ExportCsvButton data={chartData} filename="backtest_curve" columns={["date", "basket", "benchmark"]} />
            </div>
            <PriceChart
              data={chartData}
              lines={[
                { dataKey: "basket", color: "#0284c7", name: "Basket" },
                { dataKey: "benchmark", color: "#94a3b8", name: result.benchmark?.ticker ?? "Benchmark" },
              ]}
            />
          </Card>
        </motion.div>
      )}
    </div>
  );
}
