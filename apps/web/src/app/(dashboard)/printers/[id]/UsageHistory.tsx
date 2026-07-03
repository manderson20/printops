"use client";

import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ApiError,
  getPrinterCounterHistory,
  type DailyCounterDelta,
  type PageCountConfidence,
} from "@/lib/api";
import { chartChrome, hue } from "@/lib/chartColors";
import { useIsDarkMode } from "@/lib/useIsDarkMode";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { ChartTooltip } from "../../insights/charts";

const SELECT_CLASS =
  "rounded-lg border border-black/[.15] bg-white px-2 py-1 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50";

const RANGE_OPTIONS = [7, 30, 90, 180];

/** Single-series area of total_delta only — near-identical to Insights'
 * TimelineChart (one hue, no legend, the card title already names it).
 * Used when there's no confirmed copy/print split for this printer's
 * vendor (page_count_confidence is "unsupported" or null). */
function TotalOnlyChart({ data }: { data: DailyCounterDelta[] }) {
  const isDark = useIsDarkMode();
  const chrome = chartChrome(isDark);
  const color = hue("blue", isDark);
  return (
    <ResponsiveContainer width="100%" height={220}>
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
          dataKey="total_delta"
          name="Pages"
          stroke={color}
          strokeWidth={2}
          fill={color}
          fillOpacity={0.1}
          dot={false}
          activeDot={{ r: 4, stroke: chrome.tooltipBg, strokeWidth: 2 }}
          connectNulls={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Stacked two-series area (Copy + Print) — stack height reconstructs
 * the total. Fixed hue order matching the existing Color/Mono and
 * Duplex/Simplex convention in insights/page.tsx's SharePair calls:
 * primary/more-common category = blue, secondary = aqua. Used only when
 * the vendor breakdown is confirmed or best-effort — never for
 * "unsupported", where copy/print are always null anyway. */
function CopyPrintChart({ data }: { data: DailyCounterDelta[] }) {
  const isDark = useIsDarkMode();
  const chrome = chartChrome(isDark);
  const printColor = hue("blue", isDark);
  const copyColor = hue("aqua", isDark);
  return (
    <div className="flex flex-col gap-2">
      <ResponsiveContainer width="100%" height={220}>
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
            dataKey="print_delta"
            name="Print"
            stackId="usage"
            stroke={printColor}
            strokeWidth={2}
            fill={printColor}
            fillOpacity={0.5}
            dot={false}
            connectNulls={false}
          />
          <Area
            type="monotone"
            dataKey="copy_delta"
            name="Copy"
            stackId="usage"
            stroke={copyColor}
            strokeWidth={2}
            fill={copyColor}
            fillOpacity={0.5}
            dot={false}
            connectNulls={false}
          />
        </AreaChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap gap-3 text-xs text-zinc-600 dark:text-zinc-400">
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: printColor }}
          />
          Print
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: copyColor }}
          />
          Copy
        </span>
      </div>
    </div>
  );
}

export function UsageHistoryCard({
  printerId,
  confidence,
}: {
  printerId: string;
  confidence: PageCountConfidence | null;
}) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<DailyCounterDelta[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPrinterCounterHistory(printerId, days)
      .then((result) => {
        setData(result);
        setError(null);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Failed to load usage history"),
      );
  }, [printerId, days]);

  const showBreakdown = confidence === "verified" || confidence === "best_effort";

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <CardTitle>Usage Over Time</CardTitle>
        <label className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
          Range
          <select
            className={SELECT_CLASS}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            {RANGE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                Last {option} days
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <ErrorState>{error}</ErrorState>}
      {!error && data === null && <Spinner label="Loading usage history…" />}
      {!error && data !== null && data.length === 0 && (
        <EmptyState>Not enough history yet — check back after a few polling cycles.</EmptyState>
      )}
      {!error && data !== null && data.length > 0 && (
        <>{showBreakdown ? <CopyPrintChart data={data} /> : <TotalOnlyChart data={data} />}</>
      )}
    </Card>
  );
}
