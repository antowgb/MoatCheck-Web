"use client";

import { Download } from "lucide-react";
import { downloadCsv } from "@/lib/csv";

export default function ExportCsvButton<T extends object>({
  data,
  filename,
  columns,
  label = "Export CSV",
  className = "",
}: {
  data: T[];
  filename: string;
  columns?: string[];
  label?: string;
  className?: string;
}) {
  return (
    <button
      onClick={() => downloadCsv(filename, data as unknown as Record<string, unknown>[], columns)}
      disabled={data.length === 0}
      className={`inline-flex items-center gap-1.5 text-slate-500 dark:text-slate-400 px-3 py-1.5 rounded-lg text-sm hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors ${className}`}
    >
      <Download size={13} />
      {label}
    </button>
  );
}
