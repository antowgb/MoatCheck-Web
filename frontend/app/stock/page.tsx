"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import Card from "@/components/Card";
import Disclaimer from "@/components/Disclaimer";
import ExportCsvButton from "@/components/ExportCsvButton";
import PriceChart from "@/components/PriceChart";
import ScoreBadge from "@/components/ScoreBadge";
import Skeleton from "@/components/Skeleton";
import { api, type PricePoint, type StockDetail } from "@/lib/api";
import { formatDate, isStale } from "@/lib/date";

const COMPONENT_LABELS: Record<string, string> = {
  revenue_growth_yoy: "Revenue growth (YoY)",
  operating_margin: "Operating margin",
  roe_or_roic: "ROE / ROIC",
  debt_to_ebitda: "Debt/EBITDA (inverted)",
  fcf_positive: "FCF positive",
  volatility: "Volatility",
  sharpe: "Sharpe ratio",
  max_drawdown: "Max drawdown",
  fundamental: "Fundamental",
  risk: "Risk",
};
const label = (k: string) => COMPONENT_LABELS[k] ?? k;

function fmt(
  v: number | string | null | undefined,
  opts?: { pct?: boolean; big?: boolean; currency?: boolean }
): string {
  if (v === null || v === undefined) return "N/A";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  if (opts?.pct) return `${(n * 100).toFixed(1)} %`;

  const prefix = opts?.currency ? "$" : "";
  const unitSep = opts?.currency ? "" : " ";
  if (opts?.big) {
    if (Math.abs(n) >= 1e12) return `${prefix}${(n / 1e12).toFixed(2)}${unitSep}T`;
    if (Math.abs(n) >= 1e9) return `${prefix}${(n / 1e9).toFixed(2)}${unitSep}B`;
    if (Math.abs(n) >= 1e6) return `${prefix}${(n / 1e6).toFixed(2)}${unitSep}M`;
  }
  return `${prefix}${n.toFixed(2)}`;
}

function StockDetailView() {
  const ticker = useSearchParams().get("ticker")?.toUpperCase() ?? "";
  const [detail, setDetail] = useState<StockDetail | null>(null);
  const [history, setHistory] = useState<PricePoint[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ticker) return;
    api.stockDetail(ticker).then(setDetail).catch((e) => setError(String(e)));
    api.priceHistory(ticker).then(setHistory).catch(() => {});
  }, [ticker]);

  if (!ticker)
    return (
      <p className="text-slate-500 dark:text-slate-400">No ticker provided (e.g. /stock/?ticker=NVDA).</p>
    );
  if (error) return <p className="text-red-600 dark:text-rose-400 text-sm">Error: {error}</p>;
  if (!detail)
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
        </div>
        <Skeleton className="h-80" />
      </div>
    );

  if (detail.stock.asset_type === "etf")
    return (
      <div className="space-y-4">
        <Disclaimer />
        <p className="text-slate-700 dark:text-slate-300">
          <span className="font-mono font-semibold text-slate-900 dark:text-slate-50">{detail.stock.ticker}</span>
          {" - "}
          {detail.stock.name}. This is a comparison benchmark, not part of the investable universe tracked by this
          tool.
        </p>
      </div>
    );

  const f = detail.fundamentals;
  const s = detail.score;

  const pending = detail.stock.status === "pending_refresh";

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="space-y-6"
    >
      <Disclaimer />
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">
          <span className="font-mono">{detail.stock.ticker}</span>{" "}
          <span className="text-slate-500 dark:text-slate-400 font-normal text-lg">{detail.stock.name}</span>
        </h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          {detail.stock.sector} · {detail.stock.industry} · {detail.stock.currency}
        </p>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
          Fundamentals, risk metrics, and the full score breakdown for this ticker.
        </p>
        {!pending && (
          <p
            className={`text-xs mt-2 ${
              isStale(detail.stock.updated_at)
                ? "text-slate-400 dark:text-slate-600"
                : "text-slate-500 dark:text-slate-400"
            }`}
          >
            Data last updated: {formatDate(detail.stock.updated_at)}
          </p>
        )}
      </div>

      {pending && (
        <div className="bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 text-amber-900 dark:text-amber-300 text-sm rounded-xl px-4 py-3">
          Data pending for this ticker: it was just added and hasn&apos;t
          been processed by a refresh yet (depends on the remaining Alpha Vantage quota, ~3 tickers/day).
          Scores and fundamentals aren&apos;t available yet.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card title="Composite score">
          <div className="text-3xl font-bold">
            <ScoreBadge score={s?.composite_score ?? null} />
          </div>
          <p className="text-xs text-slate-400 dark:text-slate-500 mt-2 font-mono">
            Fundamental: {fmt(s?.fundamental_score)} · Risk: {fmt(s?.risk_score)}
          </p>
          <Link
            href="/methodology#composite"
            className="text-xs text-sky-600 dark:text-sky-400 hover:underline mt-2 inline-block"
          >
            How is this score calculated?
          </Link>
        </Card>
        <Card title="Risk">
          <ul className="text-sm space-y-1 font-mono text-slate-700 dark:text-slate-300">
            <li>Annualized volatility: {fmt(s?.volatility_annualized, { pct: true })}</li>
            <li>Sharpe: {fmt(s?.sharpe_ratio)}</li>
            <li>Max drawdown: {fmt(s?.max_drawdown, { pct: true })}</li>
          </ul>
        </Card>
        <Card title="Valuation">
          <ul className="text-sm space-y-1 font-mono text-slate-700 dark:text-slate-300">
            <li>Trailing P/E: {fmt(f?.pe_trailing)}</li>
            <li>Forward P/E: {fmt(f?.pe_forward)}</li>
            <li>Market cap: {fmt(f?.market_cap, { big: true, currency: true })}</li>
          </ul>
        </Card>
      </div>

      <Card title="Fundamentals">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm font-mono text-slate-700 dark:text-slate-300">
          <div>Revenue: {fmt(f?.revenue, { big: true })}</div>
          <div>Growth YoY: {fmt(f?.revenue_growth_yoy, { pct: true })}</div>
          <div>Operating margin: {fmt(f?.operating_margin, { pct: true })}</div>
          <div>Net margin: {fmt(f?.net_margin, { pct: true })}</div>
          <div>ROE: {fmt(f?.roe, { pct: true })}</div>
          <div>Net debt/EBITDA: {fmt(f?.debt_to_ebitda)}</div>
          <div>FCF: {fmt(f?.free_cash_flow, { big: true })}</div>
        </div>
      </Card>

      <Card title="Price (5 years)">
        {history.length > 0 ? (
          <>
            <div className="flex justify-end mb-2">
              <ExportCsvButton data={history} filename={`${ticker}_price_history`} columns={["date", "close"]} />
            </div>
            <PriceChart data={history} lines={[{ dataKey: "close", color: "#0284c7", name: ticker }]} />
          </>
        ) : (
          <p className="text-sm text-slate-400 dark:text-slate-600">No price history.</p>
        )}
      </Card>

      {s?.score_breakdown != null && (
        <Card title="Score breakdown">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {s.score_breakdown.fundamental && (
              <div>
                <h3 className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase mb-2">
                  Fundamental components
                </h3>
                <table className="w-full text-sm">
                  <tbody>
                    {Object.entries(s.score_breakdown.fundamental.components).map(([k, c]) => (
                      <tr key={k} className="border-b border-slate-100 dark:border-slate-800 last:border-0">
                        <td className="py-1 pr-2 text-slate-700 dark:text-slate-300">{label(k)}</td>
                        <td className="py-1 pr-2 text-slate-500 dark:text-slate-500 font-mono">
                          weight {(c.weight * 100).toFixed(0)}%
                        </td>
                        <td className="py-1 text-right font-medium font-mono text-slate-900 dark:text-slate-100">
                          {(c.normalized * 100).toFixed(0)}/100
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {s.score_breakdown.fundamental.missing.length > 0 && (
                  <p className="text-xs text-slate-400 dark:text-slate-600 mt-2">
                    Excluded (missing data): {s.score_breakdown.fundamental.missing.map(label).join(", ")}
                  </p>
                )}
              </div>
            )}
            {s.score_breakdown.risk && (
              <div>
                <h3 className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase mb-2">
                  Risk components
                </h3>
                <table className="w-full text-sm">
                  <tbody>
                    {Object.entries(s.score_breakdown.risk.components).map(([k, v]) => (
                      <tr key={k} className="border-b border-slate-100 dark:border-slate-800 last:border-0">
                        <td className="py-1 pr-2 text-slate-700 dark:text-slate-300">{label(k)}</td>
                        <td className="py-1 text-right font-medium font-mono text-slate-900 dark:text-slate-100">
                          {v.toFixed(1)}/100
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {s.score_breakdown.risk.missing.length > 0 && (
                  <p className="text-xs text-slate-400 dark:text-slate-600 mt-2">
                    Excluded (missing data): {s.score_breakdown.risk.missing.map(label).join(", ")}
                  </p>
                )}
              </div>
            )}
          </div>
          {s.score_breakdown.composite && (
            <p className="text-xs text-slate-400 dark:text-slate-600 mt-4 font-mono">
              Composite weights:{" "}
              {Object.entries(s.score_breakdown.composite.weights)
                .map(([k, w]) => `${label(k)} ${(w * 100).toFixed(0)}%`)
                .join(" + ")}
            </p>
          )}
        </Card>
      )}
    </motion.div>
  );
}

export default function StockPage() {
  return (
    <Suspense fallback={<p className="text-slate-500 dark:text-slate-400">Loading…</p>}>
      <StockDetailView />
    </Suspense>
  );
}
