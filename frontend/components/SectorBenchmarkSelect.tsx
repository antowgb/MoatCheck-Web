"use client";

import type { Stock } from "@/lib/api";

const selectCls =
  "border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-400 dark:focus:border-emerald-500 transition-colors";

interface Props {
  /** Full stock list (from api.listStocks()) — filtered here to asset_type === "etf". */
  stocks: Stock[];
  value: string;
  onChange: (ticker: string) => void;
  label?: string;
}

/**
 * Dropdown to pick which ETF to compare a stock against, overriding
 * stocks.sector_benchmark_ticker for the current session only (no
 * persistence — plain controlled React state, owned by the caller).
 */
export default function SectorBenchmarkSelect({ stocks, value, onChange, label = "Sector benchmark" }: Props) {
  // SPY isn't in this list: is_benchmark=true rows are excluded from
  // GET /api/stocks, so it's offered as a fixed extra option.
  const etfs = stocks
    .filter((s) => s.asset_type === "etf")
    .map((s) => s.ticker)
    .sort();

  return (
    <label className="text-sm">
      {label && <span className="block text-slate-500 dark:text-slate-400 mb-1">{label}</span>}
      <select className={selectCls} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="SPY">SPY (global)</option>
        {etfs.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
    </label>
  );
}
