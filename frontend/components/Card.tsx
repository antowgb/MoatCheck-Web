export default function Card({
  title,
  children,
  className = "",
  id,
}: {
  title?: string;
  children: React.ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <div
      id={id}
      className={`rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-4 shadow-sm shadow-slate-950/[0.03] dark:shadow-none transition-colors ${className}`}
    >
      {title && (
        <h2 className="text-xs font-semibold text-slate-400 dark:text-slate-500 mb-3 uppercase tracking-wide">
          {title}
        </h2>
      )}
      {children}
    </div>
  );
}
