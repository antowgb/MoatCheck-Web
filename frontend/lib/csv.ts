// Minimal CSV export: no dependency, just enough escaping (quotes, commas,
// newlines) to round-trip through Excel/Sheets. Columns are auto-derived
// from the union of keys across rows unless explicitly provided.

function escapeCsvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const raw = String(value);
  return /[",\n]/.test(raw) ? `"${raw.replace(/"/g, '""')}"` : raw;
}

export function toCsv(rows: Record<string, unknown>[], columns?: string[]): string {
  if (rows.length === 0) return "";
  const cols = columns ?? Array.from(rows.reduce((set, r) => {
    Object.keys(r).forEach((k) => set.add(k));
    return set;
  }, new Set<string>()));
  const header = cols.join(",");
  const lines = rows.map((r) => cols.map((c) => escapeCsvCell(r[c])).join(","));
  return [header, ...lines].join("\n");
}

export function downloadCsv(filename: string, rows: Record<string, unknown>[], columns?: string[]): void {
  const csv = toCsv(rows, columns);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
