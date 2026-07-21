"use client";

import Link from "next/link";
import type {
  QualitativeCategory,
  QualitativeConfidence,
  QualitativeEvent,
  QualitativeSentiment,
  QualitativeSeverity,
  QualitativeSourceType,
  QualitativeTally,
} from "@/lib/api";

// Human-readable labels (English, consistent with the rest of the UI).
export const CATEGORY_LABELS: Record<QualitativeCategory, string> = {
  dated_contract: "Dated contract",
  m_and_a: "M&A",
  regulatory_admission: "Regulatory / litigation",
  guidance: "Guidance",
  backlog: "Backlog",
  governance_risk: "Governance risk",
  activist_pressure: "Activist pressure",
  customer_concentration: "Customer concentration",
};

export const SOURCE_LABELS: Record<QualitativeSourceType, string> = {
  edgar: "SEC EDGAR",
  ir_rss: "Investor relations",
  newsletter: "Newsletter",
  press: "Press",
};

const SENTIMENT_STYLE: Record<QualitativeSentiment, { dotColor: string; badge: string; label: string }> = {
  positive: {
    dotColor: "bg-emerald-500",
    label: "Positive",
    badge: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400",
  },
  negative: {
    dotColor: "bg-rose-500",
    label: "Negative",
    badge: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-400",
  },
  neutral: {
    dotColor: "bg-slate-400 dark:bg-slate-500",
    label: "Neutral",
    badge: "bg-slate-100 text-slate-600 dark:bg-slate-500/10 dark:text-slate-400",
  },
};

/** Small colored circle (CSS, not an emoji glyph) used for sentiment indicators. */
function Dot({ className }: { className: string }) {
  return <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${className}`} aria-hidden="true" />;
}

const CONFIDENCE_LABELS: Record<QualitativeConfidence, string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};

const SEVERITY_LABELS: Record<QualitativeSeverity, string> = {
  low: "Low severity",
  medium: "Medium severity",
  high: "High severity",
};

const chip = "px-1.5 py-0.5 rounded-md text-[11px] font-medium";

/**
 * Compact tally badge: colored dots with counts (positive, negative, neutral)
 * over the window (90 days by default). A COUNT of recent events, not a
 * score. Optionally links to the ticker's full timeline. Renders "N/A" when
 * there are no counted events.
 */
export function TallyBadges({
  tally,
  ticker,
  linkToTimeline = false,
}: {
  tally: QualitativeTally | undefined;
  ticker?: string;
  linkToTimeline?: boolean;
}) {
  if (!tally) return <span className="text-slate-300 dark:text-slate-700 text-sm">N/A</span>;
  const total = tally.positive + tally.negative + tally.neutral;
  const body = (
    <span
      className="inline-flex items-center gap-2 text-sm tabular-nums text-slate-600 dark:text-slate-300"
      title={`${total} event(s) over ${tally.window_days} days (excluding low-confidence)`}
    >
      <span className="inline-flex items-center gap-1">
        <Dot className="bg-emerald-500" />
        {tally.positive}
      </span>
      <span className="inline-flex items-center gap-1">
        <Dot className="bg-rose-500" />
        {tally.negative}
      </span>
      <span className="inline-flex items-center gap-1">
        <Dot className="bg-slate-400 dark:bg-slate-500" />
        {tally.neutral}
      </span>
    </span>
  );
  if (total === 0) {
    return <span className="text-slate-300 dark:text-slate-700 text-sm" title="No recent events">N/A</span>;
  }
  if (linkToTimeline && ticker) {
    return (
      <Link href={`/stock/?ticker=${ticker}#qualitative`} className="hover:underline">
        {body}
      </Link>
    );
  }
  return body;
}

/** One event rendered as a timeline row (date, badges, summary, source link). */
export function QualitativeEventItem({ event, showTicker = false }: { event: QualitativeEvent; showTicker?: boolean }) {
  const sentiment = SENTIMENT_STYLE[event.sentiment];
  return (
    <li className="relative pl-5 pb-4 border-l border-slate-200 dark:border-slate-800 last:border-l-transparent last:pb-0">
      <span className="absolute -left-[4.5px] top-2">
        <Dot className={sentiment.dotColor} />
      </span>
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <span className="text-xs font-mono text-slate-500 dark:text-slate-400">
          {event.event_date ?? "unknown date"}
        </span>
        {showTicker && (
          <Link
            href={`/stock/?ticker=${event.ticker}`}
            className="text-xs font-mono font-medium text-emerald-600 dark:text-emerald-400 hover:underline"
          >
            {event.ticker}
          </Link>
        )}
        <span className={`${chip} bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400`}>
          {CATEGORY_LABELS[event.category] ?? event.category}
        </span>
        <span className={`${chip} ${sentiment.badge}`}>{sentiment.label}</span>
        <span
          className={`${chip} bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400`}
          title={SEVERITY_LABELS[event.severity]}
        >
          {CONFIDENCE_LABELS[event.confidence]}
        </span>
        <span className="text-[11px] text-slate-400 dark:text-slate-600">{SOURCE_LABELS[event.source_type] ?? event.source_type}</span>
      </div>
      {event.summary && <p className="text-sm text-slate-700 dark:text-slate-300">{event.summary}</p>}
      {event.source_url && (
        <a
          href={event.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-emerald-600 dark:text-emerald-400 hover:underline mt-0.5 inline-block"
        >
          Source ↗
        </a>
      )}
    </li>
  );
}
