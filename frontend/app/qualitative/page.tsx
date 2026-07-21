"use client";

import { Suspense, useEffect, useState } from "react";
import Card from "@/components/Card";
import Disclaimer from "@/components/Disclaimer";
import PageHeader from "@/components/PageHeader";
import { CATEGORY_LABELS, QualitativeEventItem } from "@/components/QualitativeBadges";
import Skeleton from "@/components/Skeleton";
import { api, type QualitativeCategory, type QualitativeEvent, type Stock } from "@/lib/api";

const inputCls =
  "border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-400 dark:focus:border-emerald-500 transition-colors";

const CATEGORIES = Object.keys(CATEGORY_LABELS) as QualitativeCategory[];

function QualitativeView() {
  const [events, setEvents] = useState<QualitativeEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState("");
  const [ticker, setTicker] = useState("");
  const [stocks, setStocks] = useState<Stock[]>([]);

  useEffect(() => {
    api
      .listStocks()
      .then((s) => setStocks([...s].sort((a, b) => a.ticker.localeCompare(b.ticker))))
      .catch(() => setStocks([]));
  }, []);

  const load = () => {
    setEvents(null);
    setError(null);
    api
      .qualitativeFeed({
        category: category || undefined,
        ticker: ticker || undefined,
        limit: 200,
      })
      .then((r) => setEvents(r.events))
      .catch((e) => setError(String(e)));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, ticker]);

  return (
    <div>
      <PageHeader
        title="Qualitative feed"
        subtitle="All AI-classified qualitative events across every ticker, newest first. An indicative count, never a score. Always verify against the source."
      />
      <Disclaimer />

      <Card className="mb-6">
        <div className="flex flex-wrap gap-3 items-end">
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Category</span>
            <select className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABELS[c]}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="block text-slate-500 dark:text-slate-400 mb-1">Ticker</span>
            <select
              className={`${inputCls} min-w-[8rem]`}
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
            >
              <option value="">All tickers</option>
              {stocks.map((s) => (
                <option key={s.ticker} value={s.ticker}>
                  {s.ticker}
                  {s.name ? ` - ${s.name}` : ""}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Card>

      {error && <p className="text-red-600 dark:text-rose-400 text-sm mb-4">Error: {error}</p>}

      <Card>
        {events === null ? (
          <Skeleton className="h-64" />
        ) : events.length === 0 ? (
          <p className="text-sm text-slate-400 dark:text-slate-600">No qualitative events.</p>
        ) : (
          <ul>
            {events.map((ev) => (
              <QualitativeEventItem key={ev.id} event={ev} showTicker />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

export default function QualitativePage() {
  return (
    <Suspense fallback={<p className="text-slate-500 dark:text-slate-400">Loading…</p>}>
      <QualitativeView />
    </Suspense>
  );
}
