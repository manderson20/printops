"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_HUES, chartChrome, hue } from "@/lib/chartColors";
import { useIsDarkMode } from "@/lib/useIsDarkMode";
import type { TimelineBucket } from "@/lib/api";

type ChartTooltipProps = {
  active?: boolean;
  label?: string;
  payload?: { value: number; name: string }[];
  chrome: ReturnType<typeof chartChrome>;
};

function ChartTooltip({ active, label, payload, chrome }: ChartTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div
      style={{
        background: chrome.tooltipBg,
        border: `1px solid ${chrome.tooltipBorder}`,
        borderRadius: 8,
        padding: "8px 12px",
        color: chrome.tooltipText,
        fontSize: 12,
      }}
    >
      <div style={{ marginBottom: 4, fontWeight: 600 }}>{label}</div>
      {payload.map((p) => (
        <div key={p.name}>
          {p.name}: {p.value.toLocaleString()}
        </div>
      ))}
    </div>
  );
}

/** "Pages printed over time" — a single-series trend, so one hue (sequential
 * job, not categorical) and no legend box (the card title already names it). */
export function TimelineChart({ data }: { data: TimelineBucket[] }) {
  const isDark = useIsDarkMode();
  const chrome = chartChrome(isDark);
  const color = hue("blue", isDark);
  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={chrome.grid} vertical={false} />
        <XAxis
          dataKey="bucket_start"
          tick={{ fill: chrome.mutedText, fontSize: 11 }}
          axisLine={{ stroke: chrome.axis }}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: chrome.mutedText, fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={44}
          allowDecimals={false}
        />
        <Tooltip content={<ChartTooltip chrome={chrome} />} />
        <Area
          type="monotone"
          dataKey="total_pages"
          name="Pages"
          stroke={color}
          strokeWidth={2}
          fill={color}
          fillOpacity={0.1}
          dot={false}
          activeDot={{ r: 4, stroke: chrome.tooltipBg, strokeWidth: 2 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Peak hours / peak day-of-week — magnitude comparison across an ordered
 * axis, single series, one hue (position/height already encodes the value). */
export function VolumeBarChart({ data }: { data: { label: string; value: number }[] }) {
  const isDark = useIsDarkMode();
  const chrome = chartChrome(isDark);
  const color = hue("blue", isDark);
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap={4}>
        <CartesianGrid stroke={chrome.grid} vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: chrome.mutedText, fontSize: 11 }}
          axisLine={{ stroke: chrome.axis }}
          tickLine={false}
          interval={0}
        />
        <YAxis
          tick={{ fill: chrome.mutedText, fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={44}
          allowDecimals={false}
        />
        <Tooltip content={<ChartTooltip chrome={chrome} />} />
        <Bar dataKey="value" name="Pages" fill={color} radius={[4, 4, 0, 0]} maxBarSize={24} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Part-to-whole share (color vs mono, duplex vs simplex) — a horizontal
 * stacked bar rather than a pie of two slices (see dataviz skill's
 * choosing-a-form guidance: a 2-slice pie is a meter/stacked-bar job).
 * Always shows its own legend (2+ series) with direct percentage labels. */
export function SharePair({
  segments,
}: {
  segments: { label: string; value: number; hueName: keyof typeof CHART_HUES }[];
}) {
  const isDark = useIsDarkMode();
  const total = segments.reduce((sum, s) => sum + s.value, 0);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex h-6 w-full gap-0.5 overflow-hidden rounded-full bg-black/[.04] dark:bg-white/[.06]">
        {segments.map((s) => {
          const pct = total > 0 ? (s.value / total) * 100 : 0;
          if (pct <= 0) return null;
          return (
            <div
              key={s.label}
              style={{ width: `${pct}%`, backgroundColor: hue(s.hueName, isDark) }}
              title={`${s.label}: ${Math.round(pct)}%`}
            />
          );
        })}
      </div>
      <div className="flex flex-wrap gap-3 text-xs text-zinc-600 dark:text-zinc-400">
        {segments.map((s) => {
          const pct = total > 0 ? Math.round((s.value / total) * 100) : 0;
          return (
            <span key={s.label} className="flex items-center gap-1.5">
              <span
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: hue(s.hueName, isDark) }}
              />
              {s.label} — {pct}%
            </span>
          );
        })}
      </div>
    </div>
  );
}
