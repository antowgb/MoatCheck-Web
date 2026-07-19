"use client";

import { X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

const STORAGE_KEY = "moatcheck_disclaimer_dismissed";

export default function Disclaimer() {
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    setDismissed(localStorage.getItem(STORAGE_KEY) === "1");
  }, []);

  if (dismissed) return null;

  return (
    <div className="relative bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 text-amber-900 dark:text-amber-300 text-sm rounded-xl pl-4 pr-10 py-3 mb-6 transition-colors">
      Personal learning tool for quant finance. This is not investment advice.
      The data and calculations may contain errors, verify independently
      before making any decision.{" "}
      <Link href="/methodology" className="underline font-medium hover:text-amber-950 dark:hover:text-amber-200">
        How these scores are calculated
      </Link>
      .
      <button
        onClick={() => {
          localStorage.setItem(STORAGE_KEY, "1");
          setDismissed(true);
        }}
        aria-label="Dismiss"
        title="Hide this message on this browser"
        className="absolute top-2.5 right-2.5 p-1 rounded-md text-amber-700/70 dark:text-amber-400/70 hover:bg-amber-100 dark:hover:bg-amber-500/20 hover:text-amber-900 dark:hover:text-amber-200 transition-colors"
      >
        <X size={14} />
      </button>
    </div>
  );
}
