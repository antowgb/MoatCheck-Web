export default function ScoreBadge({ score }: { score: number | null }) {
  if (score === null || score === undefined) {
    return <span className="text-slate-400 dark:text-slate-600 text-sm font-mono">N/A</span>;
  }
  const color =
    score >= 70
      ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400"
      : score >= 50
        ? "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400"
        : "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-400";
  return (
    <span className={`px-2 py-0.5 rounded-md text-sm font-mono font-medium tabular-nums ${color}`}>
      {score.toFixed(1)}
    </span>
  );
}
