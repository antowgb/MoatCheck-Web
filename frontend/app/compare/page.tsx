"use client";

import { Play } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import Card from "@/components/Card";
import Disclaimer from "@/components/Disclaimer";
import ExportCsvButton from "@/components/ExportCsvButton";
import PageHeader from "@/components/PageHeader";
import PriceChart from "@/components/PriceChart";
import Skeleton from "@/components/Skeleton";
import TickerCombobox, { type TickerComboboxOption } from "@/components/TickerCombobox";
import { api, type BenchmarkOption, type CompareResult, type Stock } from "@/lib/api";

const inputCls =
  "border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-400 dark:focus:border-emerald-500 transition-colors";

// Cycled by index across selected tickers — distinct enough at the small N
// (a handful of tickers) this tool is meant for.
const PALETTE = ["#0ea5e9", "#10b981", "#f59e0b", "#f43f5e", "#8b5cf6", "#06b6d4", "#84cc16", "#d946ef"];

function fmtPct(v: number | null): string {
  return v != null ? `${(v * 100).toFixed(1)} %` : "N/A";
}

export default function ComparePage() {
  const [tickerOptions, setTickerOptions] = useState<TickerComboboxOption[]>([]);
  const [tickerOptionsLoading, setTickerOptionsLoading] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const [startDate, setStartDate] = useState("2023-07-01");
  const [result, setResult] = useState<CompareResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Every ticker is eligible here (equity, ETF, and is_benchmark like SPY) —
    // there's no "investable universe" notion for a raw price comparison.
    Promise.all([api.listStocks(), api.listBenchmarks()])
      .then(([stocks, benchmarks]: [Stock[], BenchmarkOption[]]) => {
        const merged = new Map<string, TickerComboboxOption>();
        for (const s of stocks) merged.set(s.ticker, { ticker: s.ticker, name: s.name, sector: s.sector });
        for (const b of benchmarks)
          // Sector ETFs have no `sector` in the DB (Alpha Vantage OVERVIEW is never
          // called for ETFs) — use the backend-resolved sector_name instead, so e.g.
          // filtering by "Technology" still surfaces XLK alongside Technology equities.
          if (!merged.has(b.ticker))
            merged.set(b.ticker, { ticker: b.ticker, name: b.name, sector: b.sector_name ?? null });
        setTickerOptions(Array.from(merged.values()).sort((a, b) => a.ticker.localeCompare(b.ticker)));
      })
      .catch(() => setTickerOptions([]))
      .finally(() => setTickerOptionsLoading(false));
  }, []);

  const toggle = (ticker: string) => {
    setSelected((s) => (s.includes(ticker) ? s.filter((t) => t !== ticker) : [...s, ticker]));
  };

  const run = () => {
    if (selected.length < 2) {
      setError("Select at least 2 tickers to compare.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    api
      .compare(selected, startDate)
      .then(setResult)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };

  const chartData = useMemo(() => {
    if (!result?.series) return [];
    const byDate = new Map<string, Record<string, string | number>>();
    for (const s of result.series) {
      for (const p of s.curve) {
        const row = byDate.get(p.date) ?? { date: p.date };
        row[s.ticker] = p.value;
        byDate.set(p.date, row);
      }
    }
    return Array.from(byDate.values()).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  }, [result]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Compare"
        subtitle="Compare the raw price performance of any tickers over a period: no scoring, no ranking, just prices normalized to 1.0 at the start date."
      />
      <Disclaimer />

      <Card>
        <div className="mb-4">
          <TickerCombobox
            label="Tickers to compare (min. 2)"
            options={tickerOptions}
            selected={selected}
            onToggle={toggle}
            onClear={() => setSelected([])}
            loading={tickerOptionsLoading}
          />
        </div>
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

      {error && <p className="text-red-600 dark:text-rose-400 text-sm">Error: {error}</p>}
      {loading && (
        <div className="space-y-2">
          <Skeleton className="h-80" />
        </div>
      )}

      {result && !loading && (
        <div className="space-y-4">
          {result.excluded.length > 0 && (
            <p className="text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 rounded-xl p-3 text-sm">
              {result.excluded.map((e) => `${e.ticker} (${e.reason})`).join(", ")}: excluded from the chart.
            </p>
          )}
          {result.series.length > 0 ? (
            <Card>
              <div className="flex justify-end mb-2">
                <ExportCsvButton
                  data={chartData}
                  filename="compare"
                  columns={["date", ...result.series.map((s) => s.ticker)]}
                />
              </div>
              <PriceChart
                data={chartData}
                lines={result.series.map((s, i) => ({
                  dataKey: s.ticker,
                  color: PALETTE[i % PALETTE.length],
                  name: s.ticker,
                }))}
              />
              <div className="flex flex-wrap gap-x-6 gap-y-1.5 mt-4 pt-4 border-t border-slate-100 dark:border-slate-800">
                {result.series.map((s, i) => (
                  <div key={s.ticker} className="text-sm inline-flex items-center gap-1.5">
                    <span
                      className="inline-block w-2.5 h-2.5 rounded-full"
                      style={{ background: PALETTE[i % PALETTE.length] }}
                    />
                    <span className="font-mono text-slate-900 dark:text-slate-100">{s.ticker}</span>
                    <span className="text-slate-500 dark:text-slate-400">{fmtPct(s.total_return)}</span>
                  </div>
                ))}
              </div>
            </Card>
          ) : (
            <p className="text-sm text-slate-400 dark:text-slate-600">
              No ticker had usable price data over this period.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
