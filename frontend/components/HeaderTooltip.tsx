"use client";

import { useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Wraps a table header's label with a hover tooltip showing a short
 * description of the column. Rendered via a portal into document.body with
 * fixed positioning computed on hover (rather than an absolutely-positioned
 * sibling): a plain absolute/relative tooltip inside an overflow-x-auto
 * table still counts toward the scrollable container's content width even
 * while invisible (opacity-0), which made the last column's tooltip force a
 * phantom horizontal scrollbar. Portaling out of the scrollable container
 * avoids that entirely. */
export default function HeaderTooltip({ label, tooltip }: { label: ReactNode; tooltip: string }) {
  const triggerRef = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  const show = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setPos({ top: rect.bottom + 6, left: rect.left + rect.width / 2 });
  };
  const hide = () => setPos(null);

  return (
    <span
      ref={triggerRef}
      className="inline-flex items-center gap-1"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {label}
      {pos &&
        createPortal(
          <span
            role="tooltip"
            className="pointer-events-none fixed z-50 w-max max-w-[220px] -translate-x-1/2 rounded-md bg-slate-900 px-2 py-1 text-[11px] font-normal normal-case leading-snug text-white shadow-lg dark:bg-slate-700"
            style={{ top: pos.top, left: pos.left }}
          >
            {tooltip}
          </span>,
          document.body
        )}
    </span>
  );
}
