"use client";

import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { api, Stock } from "@/lib/api";

export default function AddStockForm({ adminKey, onAdded }: { adminKey: string; onAdded?: () => void }) {
  const [ticker, setTicker] = useState("");
  const [assetType, setAssetType] = useState<"equity" | "etf">("equity");
  const [sectorBenchmark, setSectorBenchmark] = useState("");
  const [sectorEtfs, setSectorEtfs] = useState<Stock[]>([]);
  const [result, setResult] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retryTarget, setRetryTarget] = useState<{ ticker: string; sectorBenchmark: string } | null>(null);
  const [retrying, setRetrying] = useState(false);

  const assignSectorBenchmark = async (targetTicker: string, benchmark: string) => {
    try {
      await api.updateStock(targetTicker, adminKey, { sector_benchmark_ticker: benchmark });
      setRetryTarget(null);
      setWarning(null);
    } catch {
      setRetryTarget({ ticker: targetTicker, sectorBenchmark: benchmark });
      setWarning(
        `${targetTicker} added, but sector benchmark assignment failed. ` +
          `Contact the curator to set it manually, or retry below.`
      );
    }
  };

  useEffect(() => {
    api
      .listStocks()
      .then((stocks) => setSectorEtfs(stocks.filter((s) => s.asset_type === "etf")))
      .catch(() => setSectorEtfs([]));
  }, [result]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = ticker.trim();
    if (!t) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setWarning(null);
    api
      .addStock(t, adminKey, assetType)
      .then(async (r) => {
        setResult(`${r.ticker}: ${r.status} (priority ${r.priority}, queued)`);
        if (sectorBenchmark) {
          await assignSectorBenchmark(r.ticker, sectorBenchmark);
        }
        setTicker("");
        setAssetType("equity");
        setSectorBenchmark("");
        onAdded?.();
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };

  const retry = () => {
    if (!retryTarget) return;
    setRetrying(true);
    assignSectorBenchmark(retryTarget.ticker, retryTarget.sectorBenchmark).finally(() => setRetrying(false));
  };

  return (
    <form onSubmit={submit} className="flex flex-wrap gap-3 items-end mb-4">
      <label className="text-sm">
        <span className="block text-slate-500 dark:text-slate-400 mb-1">Add a ticker</span>
        <input
          className="border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 uppercase text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-400 dark:focus:border-emerald-500 transition-colors"
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          placeholder="e.g. NVDA"
          maxLength={10}
        />
      </label>
      <label className="text-sm">
        <span className="block text-slate-500 dark:text-slate-400 mb-1">Asset type</span>
        <select
          className="border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-400 dark:focus:border-emerald-500 transition-colors"
          value={assetType}
          onChange={(e) => setAssetType(e.target.value as "equity" | "etf")}
        >
          <option value="equity">Equity</option>
          <option value="etf">ETF</option>
        </select>
      </label>
      <label className="text-sm">
        <span className="block text-slate-500 dark:text-slate-400 mb-1">Sector benchmark ETF</span>
        <select
          className="border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-400 dark:focus:border-emerald-500 transition-colors min-w-[10rem]"
          value={sectorBenchmark}
          onChange={(e) => setSectorBenchmark(e.target.value)}
        >
          <option value="">None</option>
          {sectorEtfs.map((s) => (
            <option key={s.ticker} value={s.ticker}>
              {s.ticker}
              {s.name ? ` - ${s.name}` : ""}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        disabled={loading || !ticker.trim()}
        className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50 transition-colors"
      >
        <Plus size={14} />
        {loading ? "Adding…" : "Add"}
      </button>
      {result && <p className="text-sm text-emerald-700 dark:text-emerald-400">{result}</p>}
      {warning && (
        <p className="text-sm text-amber-600 dark:text-amber-400 flex items-center gap-2">
          {warning}
          {retryTarget && (
            <button
              type="button"
              onClick={retry}
              disabled={retrying}
              className="underline hover:no-underline disabled:opacity-50"
            >
              {retrying ? "Retrying…" : "Retry"}
            </button>
          )}
        </p>
      )}
      {error && <p className="text-sm text-red-600 dark:text-rose-400">Error: {error}</p>}
    </form>
  );
}
