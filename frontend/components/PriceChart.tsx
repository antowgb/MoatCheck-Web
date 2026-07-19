"use client";

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

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

export default function PriceChart({
  data,
  lines,
}: {
  data: object[];
  lines: { dataKey: string; color: string; name?: string }[];
}) {
  const dark = useIsDark();

  return (
    <div className="h-80 w-full text-slate-500 dark:text-slate-400">
      <ResponsiveContainer>
        <LineChart data={data}>
          <CartesianGrid stroke="currentColor" className="text-slate-100 dark:text-slate-800" strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: "currentColor" }}
            minTickGap={60}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "currentColor" }}
            domain={["auto", "auto"]}
            width={70}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{
              background: dark ? "#0f172a" : "#ffffff",
              border: `1px solid ${dark ? "#1e293b" : "#e2e8f0"}`,
              borderRadius: 8,
              fontSize: 12,
              color: dark ? "#e2e8f0" : "#0f172a",
            }}
            labelStyle={{ color: dark ? "#94a3b8" : "#64748b" }}
            itemStyle={{ color: dark ? "#e2e8f0" : "#0f172a" }}
          />
          {lines.map((l) => (
            <Line
              key={l.dataKey}
              type="monotone"
              dataKey={l.dataKey}
              name={l.name ?? l.dataKey}
              stroke={l.color}
              dot={false}
              strokeWidth={1.5}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
