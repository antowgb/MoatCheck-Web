interface DatedClose {
  date: string;
  close: number;
}

/**
 * Total return of `benchPoints` over exactly `stockPoints`' dates
 * (forward-filled), both clipped to >= startDate. Mirrors the backend's
 * `_aligned_return` (app/backtest/engine.py) so a client-recomputed
 * comparison never drifts to a different date than the stock's.
 */
export function alignedReturn(
  stockPoints: DatedClose[],
  benchPoints: DatedClose[],
  startDate: string
): number | null {
  const stock = stockPoints.filter((p) => p.date >= startDate).sort((a, b) => a.date.localeCompare(b.date));
  if (stock.length < 2) return null;

  const bench = [...benchPoints].sort((a, b) => a.date.localeCompare(b.date));
  let bi = 0;
  let lastClose: number | null = null;
  const aligned: number[] = [];
  for (const sp of stock) {
    while (bi < bench.length && bench[bi].date <= sp.date) {
      lastClose = bench[bi].close;
      bi++;
    }
    if (lastClose != null) aligned.push(lastClose);
  }
  if (aligned.length < 2) return null;
  return aligned[aligned.length - 1] / aligned[0] - 1;
}
