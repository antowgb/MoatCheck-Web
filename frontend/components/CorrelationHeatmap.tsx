// Diverging blue (negative) -> red (positive) scale, colorblind-safe via a
// lightness gradient (not hue alone) so magnitude reads even without color.
function cellStyle(value: number): { backgroundColor: string; color: string } {
  const alpha = Math.min(1, Math.abs(value));
  const isPositive = value >= 0;
  const rgb = isPositive ? "220, 38, 38" : "37, 99, 235"; // red-600 / blue-600
  return {
    backgroundColor: `rgba(${rgb}, ${alpha})`,
    color: alpha > 0.55 ? "#fff" : "inherit",
  };
}

export default function CorrelationHeatmap({ tickers, matrix }: { tickers: string[]; matrix: number[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="border-collapse">
        <thead>
          <tr>
            <th className="px-2 py-1" />
            {tickers.map((t) => (
              <th
                key={t}
                className="px-2 py-1 text-xs font-semibold font-mono text-slate-500 dark:text-slate-400 text-center"
              >
                {t}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tickers.map((rowTicker, i) => (
            <tr key={rowTicker}>
              <th className="px-2 py-1 text-xs font-semibold font-mono text-slate-500 dark:text-slate-400 text-right whitespace-nowrap">
                {rowTicker}
              </th>
              {matrix[i].map((value, j) => (
                <td
                  key={tickers[j]}
                  className="w-16 h-12 text-center text-sm font-mono border border-white dark:border-slate-950 transition-colors"
                  style={cellStyle(value)}
                  title={`${rowTicker} vs ${tickers[j]}: ${value.toFixed(4)}`}
                >
                  {value.toFixed(2)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex items-center gap-3 mt-3 text-xs text-slate-500 dark:text-slate-400">
        <span className="inline-flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "rgba(37, 99, 235, 1)" }} />
          -1 (inverse)
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-sm border border-slate-300 dark:border-slate-700" />0
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "rgba(220, 38, 38, 1)" }} />
          +1 (perfect correlation)
        </span>
      </div>
    </div>
  );
}
