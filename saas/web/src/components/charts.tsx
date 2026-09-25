"use client";

import { useEffect, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { date, plural, rupees, rupeesShort } from "@/lib/format";

/** Chart colours are design tokens, read at runtime so the dark theme (its
 *  own validated ramp, not a flip) repaints the charts too. */
function useTokens(names: string[]) {
  const read = () => {
    if (typeof window === "undefined") return Object.fromEntries(names.map((n) => [n, "#888"]));
    const style = getComputedStyle(document.documentElement);
    return Object.fromEntries(names.map((n) => [n, style.getPropertyValue(n).trim() || "#888"]));
  };
  const [tokens, setTokens] = useState<Record<string, string>>(read);
  useEffect(() => {
    const update = () => setTokens(read());
    update();
    const mo = new MutationObserver(update);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", update);
    return () => { mo.disconnect(); mq.removeEventListener("change", update); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return tokens;
}

function TipBox({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-3 py-2 text-[12.5px] shadow-float">
      <div className="font-medium text-ink">{title}</div>
      {lines.map((l) => <div key={l} className="tnum text-ink-2">{l}</div>)}
    </div>
  );
}

const AXIS = { fontSize: 12, fill: "var(--ink-3)" };

/** Receivables aging: one ordered ramp, lighter = less late (on light). */
export function AgingChart({ data }: { data: { bucket: string; count: number; paise: number }[] }) {
  const t = useTokens(["--viz-1", "--viz-2", "--viz-3", "--viz-4", "--viz-5", "--viz-grid", "--surface"]);
  const ramp = [t["--viz-1"], t["--viz-2"], t["--viz-3"], t["--viz-4"], t["--viz-5"]];
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ top: 12, right: 8, left: 0, bottom: 0 }} barCategoryGap="18%">
        <CartesianGrid vertical={false} stroke={t["--viz-grid"]} />
        <XAxis dataKey="bucket" tickLine={false} axisLine={false} tick={AXIS} />
        <YAxis tickFormatter={(v) => rupeesShort(v)} tickLine={false} axisLine={false} tick={AXIS} width={64} />
        <Tooltip
          cursor={{ fill: "color-mix(in oklab, var(--ink) 5%, transparent)" }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const row = payload[0].payload as { bucket: string; count: number; paise: number };
            return <TipBox title={`${row.bucket} past due`} lines={[rupees(row.paise), plural(row.count, "invoice")]} />;
          }}
        />
        <Bar dataKey="paise" radius={[4, 4, 0, 0]} animationDuration={900} animationEasing="ease-out" stroke={t["--surface"]} strokeWidth={2}>
          {data.map((row, i) => <Cell key={row.bucket} fill={ramp[Math.min(i, ramp.length - 1)]} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Money in, week by week: one series, 2px line, crosshair tooltip. */
export function CollectionsChart({ data }: { data: { week_of: string; paise: number }[] }) {
  const t = useTokens(["--viz-line", "--viz-grid", "--surface"]);
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={data} margin={{ top: 12, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="collections-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={t["--viz-line"]} stopOpacity={0.28} />
            <stop offset="100%" stopColor={t["--viz-line"]} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke={t["--viz-grid"]} />
        <XAxis dataKey="week_of" tickFormatter={(v) => date(v, { day: "numeric", month: "short" })} tickLine={false} axisLine={false} tick={AXIS} minTickGap={24} />
        <YAxis tickFormatter={(v) => rupeesShort(v)} tickLine={false} axisLine={false} tick={AXIS} width={64} />
        <Tooltip
          cursor={{ stroke: "var(--ink-3)", strokeDasharray: "3 3" }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const row = payload[0].payload as { week_of: string; paise: number };
            return <TipBox title={`Week of ${date(row.week_of, { day: "numeric", month: "short" })}`} lines={[`${rupees(row.paise)} collected`]} />;
          }}
        />
        <Area type="monotone" dataKey="paise" stroke={t["--viz-line"]} strokeWidth={2} fill="url(#collections-fill)"
          activeDot={{ r: 5, stroke: t["--surface"], strokeWidth: 2 }} animationDuration={1100} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
