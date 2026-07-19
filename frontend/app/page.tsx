"use client";

import { useEffect, useMemo, useState } from "react";
import Disclaimer from "@/components/Disclaimer";
import PageHeader from "@/components/PageHeader";
import Skeleton from "@/components/Skeleton";
import StatTile from "@/components/StatTile";
import StocksTable from "@/components/StocksTable";
import { api, type Stock } from "@/lib/api";

export default function DashboardPage() {
  const [stocks, setStocks] = useState<Stock[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listStocks()
      .then(setStocks)
      .catch((e) => setError(String(e)));
  }, []);

  const stats = useMemo(() => {
    if (!stocks) return null;
    const active = stocks.filter((s) => s.status === "active");
    const scored = active.filter((s) => s.composite_score != null);
    const avg = scored.length
      ? scored.reduce((sum, s) => sum + (s.composite_score ?? 0), 0) / scored.length
      : null;
    const sectors = new Set(stocks.map((s) => s.sector).filter(Boolean));
    const pending = stocks.length - active.length;
    return { total: stocks.length, avg, sectors: sectors.size, pending };
  }, [stocks]);

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="All tracked tickers at a glance, ranked by composite score: the entry point to the rest of the tool."
      />
      <Disclaimer />
      {error && <p className="text-red-600 dark:text-rose-400 text-sm mb-4">API error: {error}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        {stats ? (
          <>
            <StatTile label="Tracked tickers" value={stats.total} />
            <StatTile label="Avg composite score" value={stats.avg != null ? stats.avg.toFixed(1) : "N/A"} />
            <StatTile label="Sectors" value={stats.sectors} />
            <StatTile
              label="Pending refresh"
              value={stats.pending}
              hint={stats.pending > 0 ? "awaiting data" : undefined}
            />
          </>
        ) : (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[68px]" />)
        )}
      </div>

      {stocks === null && !error ? (
        <div className="space-y-2">
          <Skeleton className="h-10" />
          <Skeleton className="h-64" />
        </div>
      ) : (
        <StocksTable stocks={stocks ?? []} />
      )}
    </div>
  );
}
