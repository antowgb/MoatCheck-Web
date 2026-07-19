export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// The refresh cycle normally rotates well within this window (Alpha Vantage
// daily quota + queue/maintenance ordering) — beyond it, data is likely stale.
const STALE_AFTER_DAYS = 15;

export function isStale(iso: string): boolean {
  const ageMs = Date.now() - new Date(iso).getTime();
  return ageMs > STALE_AFTER_DAYS * 24 * 60 * 60 * 1000;
}
