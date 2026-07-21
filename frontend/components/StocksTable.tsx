"use client";

import { motion } from "framer-motion";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import ExportCsvButton from "./ExportCsvButton";
import HeaderTooltip from "./HeaderTooltip";
import { TallyBadges } from "./QualitativeBadges";
import ScoreBadge from "./ScoreBadge";
import type { Stock } from "@/lib/api";
import { formatDate, isStale } from "@/lib/date";

type SortKey = "ticker" | "name" | "sector" | "composite_score" | "updated_at";

export default function StocksTable({ stocks }: { stocks: Stock[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("composite_score");
  const [asc, setAsc] = useState(false);
  const [sectorFilter, setSectorFilter] = useState("");

  const sectors = useMemo(
    () => Array.from(new Set(stocks.map((s) => s.sector).filter(Boolean))).sort() as string[],
    [stocks]
  );

  const rows = useMemo(() => {
    const filtered = sectorFilter ? stocks.filter((s) => s.sector === sectorFilter) : stocks;
    return [...filtered].sort((a, b) => {
      const va = a[sortKey];
      const vb = b[sortKey];
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      const cmp = typeof va === "number" ? va - (vb as number) : String(va).localeCompare(String(vb));
      return asc ? cmp : -cmp;
    });
  }, [stocks, sortKey, asc, sectorFilter]);

  const COLUMN_TOOLTIPS: Record<SortKey, string> = {
    ticker: "Stock ticker symbol.",
    name: "Company name.",
    sector: "GICS sector, as reported by the data provider.",
    composite_score: "Weighted blend of the fundamental (60%) and risk (40%) scores, 0-100.",
    updated_at: "Date this ticker's data was last refreshed.",
  };

  const header = (key: SortKey, label: string) => (
    <th
      className="px-3 py-2 text-left text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide cursor-pointer select-none hover:text-slate-900 dark:hover:text-slate-100 transition-colors"
      onClick={() => {
        if (sortKey === key) setAsc(!asc);
        else {
          setSortKey(key);
          setAsc(key !== "composite_score");
        }
      }}
    >
      <span className="inline-flex items-center gap-1">
        <HeaderTooltip label={label} tooltip={COLUMN_TOOLTIPS[key]} />
        {sortKey === key ? (
          asc ? <ArrowUp size={12} /> : <ArrowDown size={12} />
        ) : (
          <ArrowUpDown size={12} className="opacity-30" />
        )}
      </span>
    </th>
  );

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <select
          className="border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 text-sm bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300"
          value={sectorFilter}
          onChange={(e) => setSectorFilter(e.target.value)}
        >
          <option value="">All sectors</option>
          {sectors.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <ExportCsvButton
          data={rows}
          filename="stocks"
          columns={["ticker", "name", "sector", "industry", "composite_score", "status", "updated_at"]}
        />
      </div>
      <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
        <table className="min-w-full divide-y divide-slate-100 dark:divide-slate-800">
          <thead className="bg-slate-50/80 dark:bg-slate-800/40">
            <tr>
              {header("ticker", "Ticker")}
              {header("name", "Name")}
              {header("sector", "Sector")}
              {header("composite_score", "Composite score")}
              <th className="px-3 py-2 text-left text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">
                <HeaderTooltip
                  label="Recent events"
                  tooltip="Count of AI-classified qualitative events over 90 days (positive, negative, neutral). Indicative, not a score."
                />
              </th>
              {header("updated_at", "Updated")}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {rows.map((s, i) => (
              <motion.tr
                key={s.ticker}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.2, delay: Math.min(i, 20) * 0.015 }}
                className={
                  s.status === "pending_refresh"
                    ? "bg-amber-50/60 dark:bg-amber-500/[0.04]"
                    : "hover:bg-slate-50 dark:hover:bg-slate-800/40"
                }
              >
                <td className="px-3 py-2 font-medium font-mono">
                  <Link href={`/stock/?ticker=${s.ticker}`} className="text-emerald-600 dark:text-emerald-400 hover:underline">
                    {s.ticker}
                  </Link>
                </td>
                <td className="px-3 py-2 text-sm text-slate-700 dark:text-slate-300">{s.name ?? "N/A"}</td>
                <td className="px-3 py-2 text-sm text-slate-500 dark:text-slate-400">{s.sector ?? "N/A"}</td>
                <td className="px-3 py-2">
                  {s.status === "pending_refresh" ? (
                    <span className="px-2 py-0.5 rounded-md text-xs font-medium bg-amber-100 dark:bg-amber-500/10 text-amber-800 dark:text-amber-400">
                      Data pending
                    </span>
                  ) : (
                    <ScoreBadge score={s.composite_score} />
                  )}
                </td>
                <td className="px-3 py-2">
                  <TallyBadges tally={s.qualitative_tally} ticker={s.ticker} linkToTimeline />
                </td>
                <td className="px-3 py-2 text-sm">
                  {s.status === "pending_refresh" ? (
                    <span className="text-slate-400 dark:text-slate-600">N/A</span>
                  ) : (
                    <span
                      className={
                        isStale(s.updated_at)
                          ? "text-slate-400 dark:text-slate-600"
                          : "text-slate-500 dark:text-slate-400"
                      }
                    >
                      {formatDate(s.updated_at)}
                    </span>
                  )}
                </td>
              </motion.tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-slate-400 dark:text-slate-600 text-sm">
                  No stocks yet. Trigger a data refresh to populate the list.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
