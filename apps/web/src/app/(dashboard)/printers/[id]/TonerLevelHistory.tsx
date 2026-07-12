"use client";

import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ApiError, getPrinterTonerHistory, type DailyTonerLevel } from "@/lib/api";
import { chartChrome } from "@/lib/chartColors";
import { useIsDarkMode } from "@/lib/useIsDarkMode";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { ChartTooltip } from "../../insights/charts";

const SELECT_CLASS =
  "rounded-lg border border-black/[.15] bg-white px-2 py-1 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50";

const RANGE_OPTIONS = [7, 30, 90, 180];

type ColorSeries = { key: keyof Omit<DailyTonerLevel, "bucket_start">; label: string };

// Literal ink colors, not the app's abstract CVD-safe hue() palette — the
// point here is "this bar visually reads as the actual cartridge color,"
// not "distinguish arbitrary categories," so real toner hues win over the
// generic categorical system. Light/dark pairs keep black visible against
// a dark chart background and yellow visible against a light one.
const TONER_HEX: Record<ColorSeries["key"], { light: string; dark: string }> = {
  black: { light: "#262626", dark: "#d4d4d4" },
  cyan: { light: "#00acc1", dark: "#26c6da" },
  magenta: { light: "#d6006e", dark: "#f06292" },
  yellow: { light: "#c9a227", dark: "#ffd54f" },
};

function TonerLevelChart({ data, series }: { data: DailyTonerLevel[]; series: ColorSeries[] }) {
  const isDark = useIsDarkMode();
  const chrome = chartChrome(isDark);
  const colorFor = (key: ColorSeries["key"]) => (isDark ? TONER_HEX[key].dark : TONER_HEX[key].light);

  return (
    <div className="flex flex-col gap-2">
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barGap={2}>
          <CartesianGrid stroke={chrome.grid} vertical={false} />
          <XAxis
            dataKey="bucket_start"
            tick={{ fill: chrome.mutedText, fontSize: 11 }}
            axisLine={{ stroke: chrome.axis }}
            tickLine={false}
          />
          <YAxis
            domain={[0, 100]}
            tick={{ fill: chrome.mutedText, fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            width={36}
            allowDecimals={false}
          />
          <Tooltip content={<ChartTooltip chrome={chrome} />} />
          {series.map(({ key, label }) => (
            <Bar
              key={key}
              dataKey={key}
              name={label}
              fill={colorFor(key)}
              radius={[3, 3, 0, 0]}
              maxBarSize={20}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap gap-3 text-xs text-zinc-600 dark:text-zinc-400">
        {series.map(({ key, label }) => (
          <span key={key} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: colorFor(key) }}
            />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}

export function TonerLevelHistoryCard({
  printerId,
  colorSupported,
}: {
  printerId: string;
  colorSupported: boolean;
}) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<DailyTonerLevel[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPrinterTonerHistory(printerId, days)
      .then((result) => {
        setData(result);
        setError(null);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Failed to load toner level history"),
      );
  }, [printerId, days]);

  const series: ColorSeries[] = colorSupported
    ? [
        { key: "black", label: "Black" },
        { key: "cyan", label: "Cyan" },
        { key: "magenta", label: "Magenta" },
        { key: "yellow", label: "Yellow" },
      ]
    : [{ key: "black", label: "Black" }];

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <CardTitle>Toner Level Over Time</CardTitle>
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
      {!error && data === null && <Spinner label="Loading toner level history…" />}
      {!error && data !== null && data.length === 0 && (
        <EmptyState>
          Not enough history yet — check back after a few polling cycles, or click Detect via
          SNMP above.
        </EmptyState>
      )}
      {!error && data !== null && data.length > 0 && (
        <TonerLevelChart data={data} series={series} />
      )}
    </Card>
  );
}
