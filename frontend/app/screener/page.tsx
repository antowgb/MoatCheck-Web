"use client";

import { motion } from "framer-motion";
import { ArrowDown, ArrowUp, ArrowUpDown, RotateCcw, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Card from "@/components/Card";
import CorrelationHeatmap from "@/components/CorrelationHeatmap";
import Disclaimer from "@/components/Disclaimer";
import ExportCsvButton from "@/components/ExportCsvButton";
import HeaderTooltip from "@/components/HeaderTooltip";
import PageHeader from "@/components/PageHeader";
import ScoreBadge from "@/components/ScoreBadge";
import Skeleton from "@/components/Skeleton";
import { api, backendErrorMessage, type CorrelationResult, type ScreenerRow } from "@/lib/api";

type SortKey =
  | "composite_score"
  | "fundamental_score"
  | "risk_score"
  | "revenue_growth_yoy"
  | "market_cap"
  | "pe_trailing"
  | "debt_to_ebitda";
type SortDir = "asc" | "desc";

const SORT_COLUMNS: { key: SortKey; label: string; tooltip: string }[] = [
  { key: "composite_score", label: "Composite", tooltip: "Weighted blend of the fundamental (60%) and risk (40%) scores, 0-100." },
  { key: "fundamental_score", label: "Fundamental", tooltip: "Revenue growth, margins, ROE, leverage, and free cash flow, blended into a 0-100 score." },
  { key: "risk_score", label: "Risk", tooltip: "Volatility, Sharpe, Sortino, and max drawdown, blended into a 0-100 score." },
  { key: "revenue_growth_yoy", label: "Growth YoY", tooltip: "Revenue growth over the trailing 12 months vs. the same period a year ago." },
  { key: "market_cap", label: "Market cap", tooltip: "Shares outstanding times the latest closing price." },
  { key: "pe_trailing", label: "P/E", tooltip: "Price divided by trailing 12-month earnings per share." },
  { key: "debt_to_ebitda", label: "Debt/EBITDA", tooltip: "Net debt divided by EBITDA (TTM); lower is less leveraged. Not scored for Financial Services." },
];

const TICKER_SECTOR_TOOLTIPS: Record<"ticker" | "sector", string> = {
  ticker: "Stock ticker symbol.",
  sector: "GICS sector, as reported by the data provider.",
};

const inputCls =
  "border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-600 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500/30 focus:border-sky-400 dark:focus:border-sky-500 transition-colors";

function fmtPct(v: number | null): string {
  return v != null ? `${(v * 100).toFixed(1)} %` : "N/A";
}
// Market cap only (the sole caller of this formatter) — always shown as a dollar amount.
function fmtMarketCap(v: number | null): string {
  if (v == null) return "N/A";
  if (Math.abs(v) >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  return `$${v.toFixed(0)}`;
}
function fmtNum(v: number | null): string {
  return v != null ? v.toFixed(2) : "N/A";
}

interface Filters {
  sector: string;
  minScore: string;
  minGrowth: string;
  minRiskScore: string;
  marketCapMin: string;
  marketCapMax: string;
  peMax: string;
  debtToEbitdaMax: string;
}

const EMPTY_FILTERS: Filters = {
  sector: "",
  minScore: "",
  minGrowth: "",
  minRiskScore: "",
  marketCapMin: "",
  marketCapMax: "",
  peMax: "",
  debtToEbitdaMax: "",
};

// Mapping between filter key (React state) <-> query string parameter (URL + API).
const FILTER_PARAMS: Record<keyof Filters, string> = {
  sector: "sector",
  minScore: "min_score",
  minGrowth: "min_growth",
  minRiskScore: "min_risk_score",
  marketCapMin: "market_cap_min",
  marketCapMax: "market_cap_max",
  peMax: "pe_max",
  debtToEbitdaMax: "debt_to_ebitda_max",
};

function filtersFromSearchParams(params: URLSearchParams): Filters {
  const f = { ...EMPTY_FILTERS };
  (Object.keys(FILTER_PARAMS) as (keyof Filters)[]).forEach((k) => {
    const v = params.get(FILTER_PARAMS[k]);
    if (v !== null) f[k] = v;
  });
  return f;
}

function ScreenerView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [filters, setFilters] = useState<Filters>(() => filtersFromSearchParams(searchParams));
  const [rows, setRows] = useState<ScreenerRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>(
    () => (searchParams.get("sort") as SortKey | null) ?? "composite_score"
  );
  const [sortDir, setSortDir] = useState<SortDir>(() => (searchParams.get("dir") as SortDir | null) ?? "desc");

  const [selectedTickers, setSelectedTickers] = useState<Set<string>>(new Set());
  const [correlation, setCorrelation] = useState<CorrelationResult | null>(null);
  const [correlationError, setCorrelationError] = useState<string | null>(null);
  const [correlationLoading, setCorrelationLoading] = useState(false);

  const toggleTicker = (ticker: string) => {
    setSelectedTickers((prev) => {
      const next = new Set(prev);
      if (next.has(ticker)) next.delete(ticker);
      else next.add(ticker);
      return next;
    });
    setCorrelation(null);
    setCorrelationError(null);
  };

  const handleCompareCorrelation = () => {
    setCorrelationLoading(true);
    setCorrelationError(null);
    api
      .correlation([...selectedTickers])
      .then((res) => setCorrelation(res))
      .catch((e) => setCorrelationError(backendErrorMessage(e)))
      .finally(() => setCorrelationLoading(false));
  };

  // Applies the filters: updates the URL (shareable/reloadable view) AND
  // re-fetches the backend. Sorting, on the other hand, never re-fetches (see handleSort).
  const applyFilters = useCallback(
    (next: Filters, nextSortKey: SortKey = sortKey, nextSortDir: SortDir = sortDir) => {
      setLoading(true);
      setError(null);

      const qs = new URLSearchParams();
      (Object.keys(FILTER_PARAMS) as (keyof Filters)[]).forEach((k) => {
        if (next[k]) qs.set(FILTER_PARAMS[k], next[k]);
      });
      qs.set("sort", nextSortKey);
      qs.set("dir", nextSortDir);
      router.replace(`${pathname}?${qs.toString()}`, { scroll: false });

      api
        .screener({
          sector: next.sector || undefined,
          min_score: next.minScore ? Number(next.minScore) : undefined,
          min_growth: next.minGrowth ? Number(next.minGrowth) / 100 : undefined,
          min_risk_score: next.minRiskScore ? Number(next.minRiskScore) : undefined,
          market_cap_min: next.marketCapMin ? Number(next.marketCapMin) : undefined,
          market_cap_max: next.marketCapMax ? Number(next.marketCapMax) : undefined,
          pe_max: next.peMax ? Number(next.peMax) : undefined,
          debt_to_ebitda_max: next.debtToEbitdaMax ? Number(next.debtToEbitdaMax) : undefined,
        })
        .then((res) => {
          setRows(res.rows);
        })
        .catch((e) => setError(String(e)))
        .finally(() => setLoading(false));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pathname, router]
  );

  // Initial load: picks up filters/sort already present in the URL
  // (link sharing / reloading a filtered view).
  useEffect(() => {
    applyFilters(filters, sortKey, sortDir);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleFilterChange = (key: keyof Filters, value: string) => {
    setFilters((f) => ({ ...f, [key]: value }));
  };

  const handleReset = () => {
    setFilters(EMPTY_FILTERS);
    applyFilters(EMPTY_FILTERS);
  };

  // Sorting is 100% frontend-side (no re-fetch): we sort the already-loaded
  // table and just update the URL so the view stays shareable.
  const handleSort = (key: SortKey) => {
    const nextDir: SortDir = key === sortKey && sortDir === "desc" ? "asc" : "desc";
    setSortKey(key);
    setSortDir(nextDir);
    const qs = new URLSearchParams(searchParams.toString());
    qs.set("sort", key);
    qs.set("dir", nextDir);
    router.replace(`${pathname}?${qs.toString()}`, { scroll: false });
  };

  const sortedRows = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1; // missing values always sort last, regardless of sort direction
      if (bv == null) return -1;
      const diff = av - bv;
      return sortDir === "asc" ? diff : -diff;
    });
    return copy;
  }, [rows, sortKey, sortDir]);

  const sortIcon = (key: SortKey) =>
    key === sortKey ? (
      sortDir === "asc" ? (
        <ArrowUp size={12} />
      ) : (
        <ArrowDown size={12} />
      )
    ) : (
      <ArrowUpDown size={12} className="opacity-30" />
    );

  return (
    <div>
      <PageHeader
        title="Screener"
        subtitle="Filter and sort the tracked universe by score, growth, valuation, and leverage to narrow down candidates."
      />
      <Disclaimer />

      <Card className="mb-6">
        <div className="flex items-center gap-2 mb-3 text-slate-500 dark:text-slate-400">
          <SlidersHorizontal size={14} />
          <span className="text-xs font-semibold uppercase tracking-wide">Filters</span>
        </div>
        <div className="flex flex-wrap gap-3 items-end">
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Sector</span>
            <input
              className={inputCls}
              value={filters.sector}
              onChange={(e) => handleFilterChange("sector", e.target.value)}
              placeholder="e.g. Technology"
            />
          </label>
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Min composite score</span>
            <input
              type="number"
              className={`${inputCls} w-28`}
              value={filters.minScore}
              onChange={(e) => handleFilterChange("minScore", e.target.value)}
              placeholder="0-100"
            />
          </label>
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Min risk score</span>
            <input
              type="number"
              className={`${inputCls} w-28`}
              value={filters.minRiskScore}
              onChange={(e) => handleFilterChange("minRiskScore", e.target.value)}
              placeholder="0-100"
            />
          </label>
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Min growth (%)</span>
            <input
              type="number"
              className={`${inputCls} w-28`}
              value={filters.minGrowth}
              onChange={(e) => handleFilterChange("minGrowth", e.target.value)}
              placeholder="e.g. 10"
            />
          </label>
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Min market cap</span>
            <input
              type="number"
              className={`${inputCls} w-32`}
              value={filters.marketCapMin}
              onChange={(e) => handleFilterChange("marketCapMin", e.target.value)}
              placeholder="e.g. 1e9"
            />
          </label>
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Max market cap</span>
            <input
              type="number"
              className={`${inputCls} w-32`}
              value={filters.marketCapMax}
              onChange={(e) => handleFilterChange("marketCapMax", e.target.value)}
              placeholder="e.g. 1e12"
            />
          </label>
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Max P/E</span>
            <input
              type="number"
              className={`${inputCls} w-24`}
              value={filters.peMax}
              onChange={(e) => handleFilterChange("peMax", e.target.value)}
              placeholder="e.g. 30"
            />
          </label>
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Max Debt/EBITDA</span>
            <input
              type="number"
              className={`${inputCls} w-24`}
              value={filters.debtToEbitdaMax}
              onChange={(e) => handleFilterChange("debtToEbitdaMax", e.target.value)}
              placeholder="e.g. 3"
            />
          </label>
          <button
            onClick={() => applyFilters(filters)}
            className="bg-sky-600 hover:bg-sky-700 text-white px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
          >
            Filter
          </button>
          <button
            onClick={handleReset}
            className="inline-flex items-center gap-1.5 text-slate-500 dark:text-slate-400 px-3 py-1.5 rounded-lg text-sm hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            <RotateCcw size={13} />
            Reset
          </button>
        </div>
      </Card>

      {error && <p className="text-red-600 dark:text-rose-400 text-sm mb-4">Error: {error}</p>}

      <div className="flex justify-end mb-2">
        <ExportCsvButton
          data={sortedRows}
          filename="screener"
          columns={[
            "ticker",
            "sector",
            "composite_score",
            "fundamental_score",
            "risk_score",
            "revenue_growth_yoy",
            "market_cap",
            "pe_trailing",
            "debt_to_ebitda",
          ]}
        />
      </div>

      <Card className="mb-6">
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={handleCompareCorrelation}
            disabled={selectedTickers.size < 2 || correlationLoading}
            className="bg-sky-600 hover:bg-sky-700 disabled:bg-slate-300 dark:disabled:bg-slate-700 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
          >
            {correlationLoading ? "Comparing…" : "Compare correlation"}
          </button>
          <span className="text-sm text-slate-500 dark:text-slate-400">
            {selectedTickers.size < 2
              ? "Select at least 2 tickers in the table to compare their correlation."
              : `${selectedTickers.size} tickers selected: ${[...selectedTickers].join(", ")}`}
          </span>
        </div>

        {correlationError && (
          <p className="text-red-600 dark:text-rose-400 text-sm mt-3">Error: {correlationError}</p>
        )}

        {correlation && (
          <div className="mt-4">
            <CorrelationHeatmap tickers={correlation.tickers} matrix={correlation.matrix} />
          </div>
        )}
      </Card>

      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-10" />
          <Skeleton className="h-64" />
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
          <table className="min-w-full divide-y divide-slate-100 dark:divide-slate-800">
            <thead className="bg-slate-50/80 dark:bg-slate-800/40">
              <tr className="text-left text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">
                <th className="px-3 py-2 w-8" />
                <th className="px-3 py-2">
                  <HeaderTooltip label="Ticker" tooltip={TICKER_SECTOR_TOOLTIPS.ticker} />
                </th>
                <th className="px-3 py-2">
                  <HeaderTooltip label="Sector" tooltip={TICKER_SECTOR_TOOLTIPS.sector} />
                </th>
                {SORT_COLUMNS.map((c) => (
                  <th
                    key={c.key}
                    className="px-3 py-2 cursor-pointer select-none hover:text-slate-900 dark:hover:text-slate-100 transition-colors"
                    onClick={() => handleSort(c.key)}
                  >
                    <span className="inline-flex items-center gap-1">
                      <HeaderTooltip label={c.label} tooltip={c.tooltip} />
                      {sortIcon(c.key)}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {sortedRows.map((r, i) =>
                r.status === "pending_refresh" ? (
                  <motion.tr
                    key={r.ticker}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.2, delay: Math.min(i, 20) * 0.015 }}
                    className="bg-amber-50/60 dark:bg-amber-500/[0.04]"
                  >
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        checked={selectedTickers.has(r.ticker)}
                        onChange={() => toggleTicker(r.ticker)}
                        aria-label={`Select ${r.ticker}`}
                      />
                    </td>
                    <td className="px-3 py-2 font-medium font-mono">
                      <Link href={`/stock/?ticker=${r.ticker}`} className="text-sky-600 dark:text-sky-400 hover:underline">
                        {r.ticker}
                      </Link>
                    </td>
                    <td className="px-3 py-2 text-sm text-slate-500 dark:text-slate-400">{r.sector ?? "N/A"}</td>
                    <td className="px-3 py-2" colSpan={SORT_COLUMNS.length}>
                      <span className="px-2 py-0.5 rounded-md text-xs font-medium bg-amber-100 dark:bg-amber-500/10 text-amber-800 dark:text-amber-400">
                        Data pending
                      </span>
                    </td>
                  </motion.tr>
                ) : (
                  <motion.tr
                    key={r.ticker}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.2, delay: Math.min(i, 20) * 0.015 }}
                    className="hover:bg-slate-50 dark:hover:bg-slate-800/40"
                  >
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        checked={selectedTickers.has(r.ticker)}
                        onChange={() => toggleTicker(r.ticker)}
                        aria-label={`Select ${r.ticker}`}
                      />
                    </td>
                    <td className="px-3 py-2 font-medium font-mono">
                      <Link href={`/stock/?ticker=${r.ticker}`} className="text-sky-600 dark:text-sky-400 hover:underline">
                        {r.ticker}
                      </Link>
                    </td>
                    <td className="px-3 py-2 text-sm text-slate-500 dark:text-slate-400">{r.sector ?? "N/A"}</td>
                    <td className="px-3 py-2">
                      <ScoreBadge score={r.composite_score} />
                    </td>
                    <td className="px-3 py-2">
                      <ScoreBadge score={r.fundamental_score} />
                    </td>
                    <td className="px-3 py-2">
                      <ScoreBadge score={r.risk_score} />
                    </td>
                    <td className="px-3 py-2 text-sm font-mono text-slate-700 dark:text-slate-300">
                      {fmtPct(r.revenue_growth_yoy)}
                    </td>
                    <td className="px-3 py-2 text-sm font-mono text-slate-700 dark:text-slate-300">
                      {fmtMarketCap(r.market_cap)}
                    </td>
                    <td className="px-3 py-2 text-sm font-mono text-slate-700 dark:text-slate-300">
                      {fmtNum(r.pe_trailing)}
                    </td>
                    <td className="px-3 py-2 text-sm font-mono text-slate-700 dark:text-slate-300">
                      {fmtNum(r.debt_to_ebitda)}
                    </td>
                  </motion.tr>
                )
              )}
              {sortedRows.length === 0 && (
                <tr>
                  <td colSpan={10} className="px-3 py-6 text-center text-slate-400 dark:text-slate-600 text-sm">
                    No results.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function ScreenerPage() {
  return (
    <Suspense fallback={<p className="text-slate-500 dark:text-slate-400">Loading…</p>}>
      <ScreenerView />
    </Suspense>
  );
}
