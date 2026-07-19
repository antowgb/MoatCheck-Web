"use client";

import { useEffect, useState } from "react";
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";

function useIsDark(): boolean {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const root = document.documentElement;
    setDark(root.classList.contains("dark"));
    const observer = new MutationObserver(() => setDark(root.classList.contains("dark")));
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

interface FrontierPointDatum {
  volatility: number;
  return: number;
}

// Bigger, distinctly-colored marker for the max-Sharpe point — a plain
// Recharts `shape` string (e.g. "star") renders at the same small default
// size as the curve's dots, which wouldn't read as "highlighted" next to them.
function MaxSharpeMarker(props: unknown) {
  const { cx, cy } = props as { cx?: number; cy?: number };
  if (cx == null || cy == null) return <></>;
  return <circle cx={cx} cy={cy} r={7} fill="#f59e0b" stroke="#ffffff" strokeWidth={1.5} />;
}

export default function EfficientFrontierChart({
  frontier,
  maxSharpe,
}: {
  frontier: FrontierPointDatum[];
  maxSharpe: FrontierPointDatum;
}) {
  const dark = useIsDark();

  return (
    <div className="h-80 w-full text-slate-500 dark:text-slate-400">
      <ResponsiveContainer>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
          <CartesianGrid stroke="currentColor" className="text-slate-100 dark:text-slate-800" strokeDasharray="3 3" />
          <XAxis
            type="number"
            dataKey="volatility"
            name="Volatility"
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            tick={{ fontSize: 11, fill: "currentColor" }}
            axisLine={false}
            tickLine={false}
            label={{
              value: "Volatility (annualized)",
              position: "insideBottom",
              offset: -10,
              fontSize: 11,
              fill: "currentColor",
            }}
          />
          <YAxis
            type="number"
            dataKey="return"
            name="Expected return"
            tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
            tick={{ fontSize: 11, fill: "currentColor" }}
            width={55}
            axisLine={false}
            tickLine={false}
            label={{ value: "Expected return", angle: -90, position: "insideLeft", fontSize: 11, fill: "currentColor" }}
          />
          <Tooltip
            cursor={{ strokeDasharray: "3 3" }}
            contentStyle={{
              background: dark ? "#0f172a" : "#ffffff",
              border: `1px solid ${dark ? "#1e293b" : "#e2e8f0"}`,
              borderRadius: 8,
              fontSize: 12,
              color: dark ? "#e2e8f0" : "#0f172a",
            }}
            labelStyle={{ color: dark ? "#94a3b8" : "#64748b" }}
            itemStyle={{ color: dark ? "#e2e8f0" : "#0f172a" }}
            formatter={(value: number, name: string) => [`${(value * 100).toFixed(2)}%`, name]}
          />
          <Scatter name="Efficient frontier" data={frontier} fill="#0ea5e9" />
          <Scatter name="Max Sharpe" data={[maxSharpe]} shape={MaxSharpeMarker} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}
