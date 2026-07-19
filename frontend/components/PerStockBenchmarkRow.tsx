"use client";

import { useState } from "react";
import SectorBenchmarkSelect from "@/components/SectorBenchmarkSelect";
import { alignedReturn } from "@/lib/alignedReturn";
import { api, type BacktestResult, type Stock } from "@/lib/api";

function pct(v: number | null | undefined): string {
  return v != null ? `${(v * 100).toFixed(1)} %` : "N/A";
}

interface Props {
  row: NonNullable<BacktestResult["per_stock_vs_benchmarks"]>[number];
  startDate: string;
  stocks: Stock[];
}

/**
 * One row of the per-stock benchmark comparison table. The sector ETF
 * dropdown defaults to the row's configured sector_benchmark_ticker (or SPY);
 * switching it recomputes that stock's sector return client-side (session
 * state only, no persistence) via the existing /history endpoint, using the
 * same date-alignment logic as the backend.
 */
export default function PerStockBenchmarkRow({ row, startDate, stocks }: Props) {
  const [etf, setEtf] = useState(row.sector_benchmark_ticker ?? "SPY");
  const [sectorReturn, setSectorReturn] = useState<number | null>(row.sector_return);
  const [loading, setLoading] = useState(false);

  const isDefault = etf === (row.sector_benchmark_ticker ?? "SPY");
  const displayedReturn = isDefault ? row.sector_return : sectorReturn;
  const vsSector =
    row.stock_return != null && displayedReturn != null ? row.stock_return - displayedReturn : null;

  const onEtfChange = async (ticker: string) => {
    setEtf(ticker);
    if (ticker === (row.sector_benchmark_ticker ?? "SPY")) return; // back to backend's own value
    setLoading(true);
    try {
      const [stockHistory, etfHistory] = await Promise.all([
        api.priceHistory(row.ticker),
        api.priceHistory(ticker),
      ]);
      setSectorReturn(alignedReturn(stockHistory, etfHistory, startDate));
    } catch {
      setSectorReturn(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <tr className="border-t border-slate-100 dark:border-slate-800">
      <td className="py-2 pr-3 font-mono font-medium text-slate-900 dark:text-slate-100">{row.ticker}</td>
      <td className="py-2 pr-3 font-mono">{pct(row.stock_return)}</td>
      <td className="py-2 pr-3 font-mono">{pct(row.benchmark_return)}</td>
      <td className="py-2 pr-3 font-mono">{pct(row.vs_benchmark)}</td>
      <td className="py-2 pr-3">
        <SectorBenchmarkSelect stocks={stocks} value={etf} onChange={onEtfChange} label="" />
      </td>
      <td className="py-2 pr-3 font-mono">{loading ? "…" : pct(displayedReturn)}</td>
      <td className="py-2 pr-3 font-mono">{loading ? "…" : pct(vsSector)}</td>
    </tr>
  );
}
