"use client";

import { AlertTriangle, RefreshCw, Rss } from "lucide-react";
import { useEffect, useState } from "react";
import Card from "@/components/Card";
import { SOURCE_LABELS } from "@/components/QualitativeBadges";
import { api, backendErrorMessage, type FeedStatusRow, type Stock } from "@/lib/api";

const inputCls =
  "border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-400 dark:focus:border-emerald-500 transition-colors";

const STATUS_STYLE: Record<FeedStatusRow["status"], string> = {
  ok: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400",
  stale: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400",
  failed: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-400",
};

/** Editor for stocks.ir_rss_url (per-ticker, manual curation). */
function IrFeedEditor({ adminKey }: { adminKey: string }) {
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [ticker, setTicker] = useState("");
  const [url, setUrl] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = () =>
    api
      .listStocks()
      .then((s) => setStocks(s.filter((x) => x.asset_type === "equity")))
      .catch(() => setStocks([]));

  useEffect(() => {
    load();
  }, []);

  // Pre-fill the URL field with the selected ticker's current value.
  useEffect(() => {
    const s = stocks.find((x) => x.ticker === ticker);
    setUrl(s?.ir_rss_url ?? "");
  }, [ticker, stocks]);

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticker) return;
    setSaving(true);
    setMsg(null);
    setError(null);
    api
      .updateStock(ticker, adminKey, { ir_rss_url: url.trim() || null })
      .then(() => {
        setMsg(`${ticker}: IR feed ${url.trim() ? "saved" : "cleared"}.`);
        return load();
      })
      .catch((err) => setError(backendErrorMessage(err)))
      .finally(() => setSaving(false));
  };

  return (
    <Card className="mb-6">
      <div className="flex items-center gap-2 mb-3 text-slate-500 dark:text-slate-400">
        <Rss size={14} />
        <span className="text-xs font-semibold uppercase tracking-wide">IR RSS feed per ticker</span>
      </div>
      <form onSubmit={save} className="flex flex-wrap gap-3 items-end">
        <label className="text-sm">
          <span className="block text-slate-500 dark:text-slate-400 mb-1">Ticker</span>
          <select className={inputCls} value={ticker} onChange={(e) => setTicker(e.target.value)}>
            <option value="">Select…</option>
            {stocks.map((s) => (
              <option key={s.ticker} value={s.ticker}>
                {s.ticker}
                {s.ir_rss_url ? " ●" : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm flex-1 min-w-[16rem]">
          <span className="block text-slate-500 dark:text-slate-400 mb-1">IR RSS/Atom URL (empty to clear)</span>
          <input
            className={`${inputCls} w-full`}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://investors.example.com/rss"
          />
        </label>
        <button
          type="submit"
          disabled={!ticker || saving}
          className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </form>
      {msg && <p className="text-sm text-emerald-700 dark:text-emerald-400 mt-2">{msg}</p>}
      {error && <p className="text-sm text-red-600 dark:text-rose-400 mt-2">Error: {error}</p>}
      <p className="text-xs text-slate-400 dark:text-slate-600 mt-2">
        Dot (●) marks tickers that already have an IR feed configured. The IR RSS source is disabled by default
        (feature flag) until enabled in the backend config.
      </p>
    </Card>
  );
}

/** Read-only monitor of feed_status rows (surfaces failed/stale feeds). */
function FeedStatusMonitor({ adminKey }: { adminKey: string }) {
  const [feeds, setFeeds] = useState<FeedStatusRow[] | null>(null);
  const [onlyProblems, setOnlyProblems] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState<string | null>(null);

  const load = () => {
    setError(null);
    api
      .feedStatus(adminKey, onlyProblems)
      .then((r) => setFeeds(r.feeds))
      .catch((e) => setError(backendErrorMessage(e)));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onlyProblems]);

  const runRefresh = () => {
    setRunning(true);
    setRunMsg(null);
    api
      .qualitativeRefresh(adminKey)
      .then((r) => setRunMsg(`Qualitative refresh done: ${JSON.stringify(r)}`))
      .catch((e) => setError(backendErrorMessage(e)))
      .finally(() => {
        setRunning(false);
        load();
      });
  };

  return (
    <Card>
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400">
          <AlertTriangle size={14} />
          <span className="text-xs font-semibold uppercase tracking-wide">Feed status</span>
        </div>
        <div className="flex items-center gap-3">
          <label className="text-xs text-slate-500 dark:text-slate-400 flex items-center gap-1.5">
            <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
            Problems only
          </label>
          <button
            onClick={load}
            className="text-sm text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 underline"
          >
            Reload
          </button>
          <button
            onClick={runRefresh}
            disabled={running}
            className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
          >
            <RefreshCw size={13} className={running ? "animate-spin" : ""} />
            {running ? "Running…" : "Run qualitative refresh"}
          </button>
        </div>
      </div>
      {runMsg && <p className="text-xs text-emerald-700 dark:text-emerald-400 mb-2 break-all">{runMsg}</p>}
      {error && <p className="text-sm text-red-600 dark:text-rose-400 mb-2">Error: {error}</p>}
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-800">
              <th className="py-2 pr-4">Ticker</th>
              <th className="py-2 pr-4">Source</th>
              <th className="py-2 pr-4">Status</th>
              <th className="py-2 pr-4">Last success</th>
              <th className="py-2">Last error</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {(feeds ?? []).map((f) => (
              <tr key={f.id}>
                <td className="py-2 pr-4 font-mono">{f.ticker}</td>
                <td className="py-2 pr-4">{SOURCE_LABELS[f.source_type] ?? f.source_type}</td>
                <td className="py-2 pr-4">
                  <span className={`px-1.5 py-0.5 rounded-md text-xs font-medium ${STATUS_STYLE[f.status]}`}>
                    {f.status}
                  </span>
                </td>
                <td className="py-2 pr-4 text-slate-500 dark:text-slate-400">
                  {f.last_success_at ? f.last_success_at.slice(0, 10) : "N/A"}
                </td>
                <td className="py-2 text-slate-500 dark:text-slate-400 max-w-xs truncate" title={f.last_error ?? ""}>
                  {f.last_error ?? "N/A"}
                </td>
              </tr>
            ))}
            {feeds !== null && feeds.length === 0 && (
              <tr>
                <td colSpan={5} className="py-6 text-center text-slate-400 dark:text-slate-600">
                  {onlyProblems ? "No failed or stale feeds." : "No feed status recorded yet."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export default function AdminQualitativePanels({ adminKey }: { adminKey: string }) {
  return (
    <div className="mt-8">
      <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-3">Qualitative layer (V2)</h2>
      <IrFeedEditor adminKey={adminKey} />
      <FeedStatusMonitor adminKey={adminKey} />
    </div>
  );
}
