"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export interface TickerComboboxOption {
  ticker: string;
  name: string | null;
  sector: string | null;
}

interface Props {
  label: string;
  options: TickerComboboxOption[];
  selected: string[];
  onToggle: (ticker: string) => void;
  onClear: () => void;
  emptyLabel?: string;
  loading?: boolean;
}

const inputCls =
  "border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-600 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500/30 focus:border-sky-400 dark:focus:border-sky-500 transition-colors";

export default function TickerCombobox({
  label,
  options,
  selected,
  onToggle,
  onClear,
  emptyLabel = "No ticker tracked yet.",
  loading = false,
}: Props) {
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const sectors = useMemo(
    () => Array.from(new Set(options.map((o) => o.sector).filter((s): s is string => !!s))).sort(),
    [options]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return options.filter((o) => {
      if (sector && o.sector !== sector) return false;
      if (!q) return true;
      return o.ticker.toLowerCase().includes(q) || (o.name ?? "").toLowerCase().includes(q);
    });
  }, [options, query, sector]);

  return (
    <div ref={containerRef} className="relative">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-slate-500 dark:text-slate-400">{label}</span>
        {selected.length > 0 && (
          <button
            onClick={onClear}
            className="text-xs text-slate-400 dark:text-slate-600 hover:text-slate-700 dark:hover:text-slate-300 underline"
          >
            Clear ({selected.length} selected)
          </button>
        )}
      </div>

      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {selected.map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1 text-xs font-mono bg-sky-50 dark:bg-sky-500/10 text-sky-700 dark:text-sky-400 border border-sky-200 dark:border-sky-500/20 rounded-md px-2 py-0.5"
            >
              {t}
              <button
                onClick={() => onToggle(t)}
                className="hover:text-sky-900 dark:hover:text-sky-200"
                aria-label={`Remove ${t}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <input
          className={`${inputCls} flex-1`}
          placeholder={loading ? "Loading tickers…" : "Search ticker or name…"}
          value={query}
          disabled={loading}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
        />
        {sectors.length > 0 && (
          <select className={`${inputCls} w-44`} value={sector} onChange={(e) => setSector(e.target.value)}>
            <option value="">All sectors</option>
            {sectors.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        )}
      </div>

      {open && (
        <div className="absolute z-10 mt-1 w-full max-h-56 overflow-y-auto border border-slate-200 dark:border-slate-800 rounded-lg bg-white dark:bg-slate-900 shadow-lg">
          {filtered.length === 0 && (
            <p className="text-sm text-slate-400 dark:text-slate-600 px-3 py-2">
              {loading ? "Loading tickers…" : options.length === 0 ? emptyLabel : "No match."}
            </p>
          )}
          {filtered.map((o) => (
            <label
              key={o.ticker}
              className="flex items-center gap-2 text-sm px-3 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-800/60 cursor-pointer"
            >
              <input type="checkbox" checked={selected.includes(o.ticker)} onChange={() => onToggle(o.ticker)} />
              <span className="font-mono">{o.ticker}</span>
              {o.name && <span className="text-slate-500 dark:text-slate-400 truncate">{o.name}</span>}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
