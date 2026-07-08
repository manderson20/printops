"use client";

import { useEffect, useMemo, useState } from "react";
import Image from "next/image";
import {
  ApiError,
  createReportSnapshot,
  deleteReportSnapshot,
  downloadReportCsv,
  getCostBreakdown,
  getReportFunFacts,
  getReportPeakTimes,
  getReportSummary,
  getReportTimeline,
  listGoogleWorkspaceUsers,
  listPrinters,
  listReportSnapshots,
  type CostEntry,
  type GoogleWorkspaceUserEntry,
  type PeakTimes,
  type Printer,
  type ReportFilters,
  type ReportGranularity,
  type ReportSnapshot,
  type ReportSummary,
  type TimelineBucket,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Field";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { SharePair, TimelineChart, VolumeBarChart } from "./charts";
import { CombinedUsageSection } from "./CombinedUsageSection";

type DatePreset =
  | "today"
  | "week"
  | "month"
  | "semester_fall"
  | "semester_spring"
  | "school_year"
  | "all"
  | "custom";

const PRESET_OPTIONS: { value: DatePreset; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "week", label: "Last 7 days" },
  { value: "month", label: "Last 30 days" },
  { value: "semester_fall", label: "Fall semester" },
  { value: "semester_spring", label: "Spring semester" },
  { value: "school_year", label: "School year" },
  { value: "all", label: "All time" },
  { value: "custom", label: "Custom range" },
];

const PERIOD_LABELS: Record<DatePreset, string> = {
  today: "day",
  week: "week",
  month: "month",
  semester_fall: "semester",
  semester_spring: "semester",
  school_year: "school year",
  all: "period",
  custom: "period",
};

function defaultGranularity(preset: DatePreset): ReportGranularity {
  if (
    preset === "today" ||
    preset === "week" ||
    preset === "month" ||
    preset === "custom"
  ) {
    return "day";
  }
  return "week";
}

function computeRange(
  preset: DatePreset,
  customStart: string,
  customEnd: string,
): { start: Date; end: Date } | null {
  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  );
  const tomorrow = new Date(startOfToday);
  tomorrow.setDate(tomorrow.getDate() + 1);

  switch (preset) {
    case "today":
      return { start: startOfToday, end: tomorrow };
    case "week": {
      const start = new Date(startOfToday);
      start.setDate(start.getDate() - 7);
      return { start, end: tomorrow };
    }
    case "month": {
      const start = new Date(startOfToday);
      start.setDate(start.getDate() - 30);
      return { start, end: tomorrow };
    }
    case "semester_fall": {
      const y = now.getFullYear();
      return { start: new Date(y, 7, 1), end: new Date(y, 11, 31, 23, 59, 59) };
    }
    case "semester_spring": {
      const y = now.getFullYear();
      return { start: new Date(y, 0, 1), end: new Date(y, 5, 30, 23, 59, 59) };
    }
    case "school_year": {
      const y = now.getMonth() >= 7 ? now.getFullYear() : now.getFullYear() - 1;
      return {
        start: new Date(y, 7, 1),
        end: new Date(y + 1, 6, 31, 23, 59, 59),
      };
    }
    case "all":
      return { start: new Date(2000, 0, 1), end: tomorrow };
    case "custom": {
      if (!customStart || !customEnd) return null;
      const start = new Date(customStart);
      const end = new Date(customEnd);
      end.setDate(end.getDate() + 1);
      return { start, end };
    }
  }
}

function formatCurrency(value: number): string {
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "danger";
}) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-zinc-500">{label}</p>
      <p
        className={`mt-1 text-2xl font-semibold ${tone === "danger" ? "text-red-600 dark:text-red-400" : "text-black dark:text-zinc-50"}`}
      >
        {value}
      </p>
    </Card>
  );
}

type ReportData = {
  summary: ReportSummary;
  timeline: TimelineBucket[];
  printerCosts: CostEntry[];
  userCosts: CostEntry[];
  peakTimes: PeakTimes;
  funFacts: string[];
};

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; data: ReportData }
  | { phase: "error"; message: string };

export default function InsightsPage() {
  const isAdmin = useCurrentUser()?.role === "admin";

  const [preset, setPreset] = useState<DatePreset>("month");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [granularity, setGranularity] = useState<ReportGranularity>("day");

  const [printers, setPrinters] = useState<Printer[]>([]);
  const [roster, setRoster] = useState<GoogleWorkspaceUserEntry[]>([]);
  const [building, setBuilding] = useState("");
  const [department, setDepartment] = useState("");
  const [printerId, setPrinterId] = useState("");
  const [submittedBy, setSubmittedBy] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [colorMode, setColorMode] = useState("");
  const [duplexFilter, setDuplexFilter] = useState("");
  const [leaderboardType, setLeaderboardType] = useState<"printer" | "user">(
    "printer",
  );

  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [exporting, setExporting] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(true);

  // Recharts' ResponsiveContainer measures its container once and doesn't
  // re-measure just because @media print changed the layout (hiding the nav
  // and collapsing the filter panel frees up width, but there's no resize
  // event for print). Remounting the chart cards right as print starts forces
  // a fresh measurement against the actual print layout instead of the
  // narrower on-screen one.
  const [printKey, setPrintKey] = useState(0);
  useEffect(() => {
    function bump() {
      setPrintKey((k) => k + 1);
    }
    window.addEventListener("beforeprint", bump);
    window.addEventListener("afterprint", bump);
    return () => {
      window.removeEventListener("beforeprint", bump);
      window.removeEventListener("afterprint", bump);
    };
  }, []);

  useEffect(() => {
    listPrinters()
      .then(setPrinters)
      .catch(() => setPrinters([]));
    if (isAdmin) {
      listGoogleWorkspaceUsers()
        .then(setRoster)
        .catch(() => setRoster([]));
    }
  }, [isAdmin]);

  const buildings = useMemo(
    () =>
      [
        ...new Set(
          printers.map((p) => p.building).filter((b): b is string => !!b),
        ),
      ].sort(),
    [printers],
  );
  const departments = useMemo(
    () =>
      [
        ...new Set(
          printers.map((p) => p.department).filter((d): d is string => !!d),
        ),
      ].sort(),
    [printers],
  );

  const range = useMemo(
    () => computeRange(preset, customStart, customEnd),
    [preset, customStart, customEnd],
  );
  const periodLabel = PERIOD_LABELS[preset];

  // Compact one-line stand-in for the filter panel when it's hidden or when
  // printing — keeps the report self-describing without eating page width.
  const filterSummary = useMemo(() => {
    const parts: string[] = [];
    if (preset === "custom" && customStart && customEnd) {
      parts.push(`${customStart} – ${customEnd}`);
    } else {
      parts.push(
        PRESET_OPTIONS.find((o) => o.value === preset)?.label ?? preset,
      );
    }
    parts.push(
      `Granularity: ${granularity[0].toUpperCase()}${granularity.slice(1)}`,
    );
    if (isAdmin && building) parts.push(`Building: ${building}`);
    if (isAdmin && department) parts.push(`Department: ${department}`);
    if (isAdmin && printerId) {
      parts.push(
        `Printer: ${printers.find((p) => p.id === printerId)?.name ?? printerId}`,
      );
    }
    if (isAdmin && submittedBy) parts.push(`User: ${submittedBy}`);
    if (statusFilter)
      parts.push(
        `Status: ${statusFilter[0].toUpperCase()}${statusFilter.slice(1)}`,
      );
    if (colorMode)
      parts.push(`Color: ${colorMode[0].toUpperCase()}${colorMode.slice(1)}`);
    if (duplexFilter)
      parts.push(duplexFilter === "true" ? "Duplex only" : "Simplex only");
    return parts.join(" · ");
  }, [
    preset,
    customStart,
    customEnd,
    granularity,
    isAdmin,
    building,
    department,
    printerId,
    submittedBy,
    statusFilter,
    colorMode,
    duplexFilter,
    printers,
  ]);

  const filters: ReportFilters = useMemo(
    () => ({
      start: range?.start.toISOString(),
      end: range?.end.toISOString(),
      building: isAdmin && building ? building : undefined,
      department: isAdmin && department ? department : undefined,
      printer_id: isAdmin && printerId ? printerId : undefined,
      submitted_by: isAdmin && submittedBy ? submittedBy : undefined,
      status: statusFilter || undefined,
      color_mode: colorMode || undefined,
      duplex: duplexFilter ? duplexFilter === "true" : undefined,
    }),
    [
      range,
      isAdmin,
      building,
      department,
      printerId,
      submittedBy,
      statusFilter,
      colorMode,
      duplexFilter,
    ],
  );

  // Reset to "loading" the instant any of this report's inputs change,
  // computed during render rather than via an effect + setState — see
  // useCurrentUser.ts for the same pattern and why (avoids a render or two
  // of stale prior-filter data before the new fetch resolves). Bundled into
  // one JSON key since there are several independent inputs here.
  const loadKey = JSON.stringify([filters, granularity, periodLabel, preset, range]);
  const [prevLoadKey, setPrevLoadKey] = useState(loadKey);
  if (loadKey !== prevLoadKey) {
    setPrevLoadKey(loadKey);
    if (!(preset === "custom" && !range)) {
      setState({ phase: "loading" });
    }
  }

  useEffect(() => {
    if (preset === "custom" && !range) return;
    Promise.all([
      getReportSummary(filters),
      getReportTimeline(granularity, filters),
      getCostBreakdown("printer", filters),
      getCostBreakdown("user", filters),
      getReportPeakTimes(filters),
      getReportFunFacts(periodLabel, filters),
    ])
      .then(
        ([summary, timeline, printerCosts, userCosts, peakTimes, funFacts]) =>
          setState({
            phase: "ok",
            data: {
              summary,
              timeline,
              printerCosts,
              userCosts,
              peakTimes,
              funFacts,
            },
          }),
      )
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message:
            error instanceof Error
              ? error.message
              : "Failed to load report data",
        }),
      );
  }, [filters, granularity, periodLabel, preset, range]);

  async function handleExportCsv() {
    setExporting(true);
    try {
      await downloadReportCsv(filters);
    } finally {
      setExporting(false);
    }
  }

  const dayOfWeekData = useMemo(() => {
    const labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    if (state.phase !== "ok") return [];
    return labels.map((label, i) => ({
      label,
      value: state.data.peakTimes.by_day_of_week[i] ?? 0,
    }));
  }, [state]);

  const hourData = useMemo(() => {
    if (state.phase !== "ok") return [];
    return Array.from({ length: 24 }, (_, h) => ({
      label: `${h}`,
      value: state.data.peakTimes.by_hour[h] ?? 0,
    }));
  }, [state]);

  return (
    <div className="flex w-full max-w-7xl flex-col gap-6">
      {/* Print-only report header — never shown on screen. */}
      <div className="hidden items-center gap-3 border-b border-black/20 pb-4 print:flex">
        <Image src="/printops-logo.png" alt="PrintOps" width={40} height={40} />
        <div>
          <p className="text-lg font-semibold text-black">
            PrintOps — Print Insights Report
          </p>
          <p className="text-xs text-zinc-600">
            Generated {new Date().toLocaleString()}
          </p>
        </div>
      </div>
      {/* Compact stand-in for the filter panel below when printing, so the
          report stays self-describing without spending page width on the
          full form. */}
      <p className="hidden text-xs text-zinc-600 print:block">
        <strong>Filters:</strong> {filterSummary}
      </p>

      <div className="flex items-center justify-between print:hidden">
        <div>
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">
            Print Insights
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Printing activity, trends, and savings —{" "}
            {isAdmin ? "org-wide" : "your own history"}.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            onClick={handleExportCsv}
            disabled={exporting}
          >
            {exporting ? "Exporting…" : "Export CSV"}
          </Button>
          <Button variant="secondary" onClick={() => window.print()}>
            Print Summary
          </Button>
        </div>
      </div>

      <Card className="print:hidden">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
            Filters
          </h2>
          <Button
            variant="secondary"
            className="!px-2 !py-1 text-xs"
            onClick={() => setFiltersOpen((v) => !v)}
          >
            {filtersOpen ? "Hide filters" : "Show filters"}
          </Button>
        </div>

        {!filtersOpen && (
          <p className="mt-1 text-xs text-zinc-500">{filterSummary}</p>
        )}

        {filtersOpen && (
          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
            <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
              Date range
              <select
                value={preset}
                onChange={(e) => {
                  const next = e.target.value as DatePreset;
                  setPreset(next);
                  setGranularity(defaultGranularity(next));
                }}
                className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
              >
                {PRESET_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>

            {preset === "custom" && (
              <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
                Custom range
                <div className="flex gap-2">
                  <Input
                    type="date"
                    value={customStart}
                    onChange={(e) => setCustomStart(e.target.value)}
                  />
                  <Input
                    type="date"
                    value={customEnd}
                    onChange={(e) => setCustomEnd(e.target.value)}
                  />
                </div>
              </label>
            )}

            <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
              Granularity
              <select
                value={granularity}
                onChange={(e) =>
                  setGranularity(e.target.value as ReportGranularity)
                }
                className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
              >
                <option value="day">Day</option>
                <option value="week">Week</option>
                <option value="month">Month</option>
              </select>
            </label>

            {isAdmin && (
              <>
                <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
                  Building
                  <select
                    value={building}
                    onChange={(e) => setBuilding(e.target.value)}
                    className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
                  >
                    <option value="">All buildings</option>
                    {buildings.map((b) => (
                      <option key={b} value={b}>
                        {b}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
                  Department
                  <select
                    value={department}
                    onChange={(e) => setDepartment(e.target.value)}
                    className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
                  >
                    <option value="">All departments</option>
                    {departments.map((d) => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
                  Printer
                  <select
                    value={printerId}
                    onChange={(e) => setPrinterId(e.target.value)}
                    className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
                  >
                    <option value="">All printers</option>
                    {printers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
                  User
                  <input
                    list="insights-roster"
                    value={submittedBy}
                    onChange={(e) => setSubmittedBy(e.target.value)}
                    placeholder="user@domain.com"
                    className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
                  />
                  <datalist id="insights-roster">
                    {roster.map((u) => (
                      <option key={u.email} value={u.email}>
                        {u.name ?? u.email}
                      </option>
                    ))}
                  </datalist>
                </label>
              </>
            )}

            <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
              Job status
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
              >
                <option value="">Any status</option>
                <option value="forwarded">Forwarded</option>
                <option value="failed">Failed</option>
                <option value="cancelled">Cancelled</option>
              </select>
            </label>

            <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
              Color mode
              <select
                value={colorMode}
                onChange={(e) => setColorMode(e.target.value)}
                className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
              >
                <option value="">Any</option>
                <option value="color">Color</option>
                <option value="monochrome">Monochrome</option>
              </select>
            </label>

            <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
              Duplex
              <select
                value={duplexFilter}
                onChange={(e) => setDuplexFilter(e.target.value)}
                className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
              >
                <option value="">Any</option>
                <option value="true">Duplex</option>
                <option value="false">Simplex</option>
              </select>
            </label>
          </div>
        )}
      </Card>

      {state.phase === "loading" && <Spinner label="Loading report…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            <StatCard
              label="Total jobs"
              value={state.data.summary.total_jobs.toLocaleString()}
            />
            <StatCard
              label="Total pages"
              value={state.data.summary.total_pages.toLocaleString()}
            />
            <StatCard
              label="Color pages"
              value={state.data.summary.color_pages.toLocaleString()}
            />
            <StatCard
              label="Mono pages"
              value={state.data.summary.mono_pages.toLocaleString()}
            />
            <StatCard
              label="Duplex pages"
              value={state.data.summary.duplex_pages.toLocaleString()}
            />
            <StatCard
              label="Simplex pages"
              value={state.data.summary.simplex_pages.toLocaleString()}
            />
            <StatCard
              label="Failed / cancelled"
              value={`${state.data.summary.failed_jobs} / ${state.data.summary.cancelled_jobs}`}
              tone={state.data.summary.failed_jobs > 0 ? "danger" : undefined}
            />
            <StatCard
              label="Estimated cost"
              value={formatCurrency(state.data.summary.estimated_cost_total)}
            />
          </div>

          {state.data.funFacts.length > 0 && (
            <Card>
              <CardTitle className="mb-3">Fun Facts</CardTitle>
              <ul className="flex flex-col gap-2 text-sm text-zinc-700 dark:text-zinc-300">
                {state.data.funFacts.map((fact) => (
                  <li key={fact} className="flex items-start gap-2">
                    <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                    {fact}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card
            key={`timeline-${printKey}`}
            className="print:break-inside-avoid"
          >
            <CardTitle className="mb-3">Pages Printed Over Time</CardTitle>
            {state.data.timeline.length === 0 ? (
              <EmptyState>No jobs in this range.</EmptyState>
            ) : (
              <TimelineChart data={state.data.timeline} />
            )}
          </Card>

          <div
            key={`share-${printKey}`}
            className="grid grid-cols-1 gap-6 md:grid-cols-2"
          >
            <Card className="print:break-inside-avoid">
              <CardTitle className="mb-3">Color vs Monochrome</CardTitle>
              <SharePair
                segments={[
                  {
                    label: "Monochrome",
                    value: state.data.summary.mono_pages,
                    hueName: "blue",
                  },
                  {
                    label: "Color",
                    value: state.data.summary.color_pages,
                    hueName: "aqua",
                  },
                ]}
              />
            </Card>
            <Card className="print:break-inside-avoid">
              <CardTitle className="mb-3">Duplex vs Simplex</CardTitle>
              <SharePair
                segments={[
                  {
                    label: "Simplex",
                    value: state.data.summary.simplex_pages,
                    hueName: "blue",
                  },
                  {
                    label: "Duplex",
                    value: state.data.summary.duplex_pages,
                    hueName: "aqua",
                  },
                ]}
              />
            </Card>
          </div>

          <div
            key={`peak-${printKey}`}
            className="grid grid-cols-1 gap-6 md:grid-cols-2"
          >
            <Card className="print:break-inside-avoid">
              <CardTitle className="mb-3">Peak Hours</CardTitle>
              <VolumeBarChart data={hourData} />
            </Card>
            <Card className="print:break-inside-avoid">
              <CardTitle className="mb-3">Peak Day of Week</CardTitle>
              <VolumeBarChart data={dayOfWeekData} />
            </Card>
          </div>

          <Card>
            <div className="mb-3 flex items-center justify-between">
              <CardTitle>Leaderboard &amp; Cost</CardTitle>
              <div className="flex gap-1 print:hidden">
                <Button
                  variant={
                    leaderboardType === "printer" ? "primary" : "secondary"
                  }
                  className="!px-3 !py-1 text-xs"
                  onClick={() => setLeaderboardType("printer")}
                >
                  Printers
                </Button>
                <Button
                  variant={leaderboardType === "user" ? "primary" : "secondary"}
                  className="!px-3 !py-1 text-xs"
                  onClick={() => setLeaderboardType("user")}
                >
                  Users
                </Button>
              </div>
            </div>
            {(leaderboardType === "printer"
              ? state.data.printerCosts
              : state.data.userCosts
            ).length === 0 ? (
              <EmptyState>No data for this range.</EmptyState>
            ) : (
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                    <th className="py-2 font-medium">
                      {leaderboardType === "printer" ? "Printer" : "User"}
                    </th>
                    <th className="py-2 font-medium">Jobs</th>
                    <th className="py-2 font-medium">Pages</th>
                    <th className="py-2 font-medium">Toner Cost</th>
                    <th className="py-2 font-medium">Paper Cost</th>
                    <th className="py-2 font-medium">Total Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {(leaderboardType === "printer"
                    ? state.data.printerCosts
                    : state.data.userCosts
                  ).map((entry) => (
                    <tr
                      key={entry.key}
                      className="border-b border-black/[.08] last:border-0 dark:border-white/[.145]"
                    >
                      <td className="py-2 text-black dark:text-zinc-50">
                        {entry.label}
                      </td>
                      <td className="py-2 text-zinc-600 dark:text-zinc-400">
                        {entry.job_count}
                      </td>
                      <td className="py-2 text-zinc-600 dark:text-zinc-400">
                        {entry.page_count.toLocaleString()}
                      </td>
                      <td className="py-2 text-zinc-600 dark:text-zinc-400">
                        {formatCurrency(entry.toner_cost)}
                      </td>
                      <td className="py-2 text-zinc-600 dark:text-zinc-400">
                        {formatCurrency(entry.paper_cost)}
                      </td>
                      <td className="py-2 font-medium text-black dark:text-zinc-50">
                        {formatCurrency(entry.total_cost)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <Card>
            <CardTitle className="mb-3">
              Environmental &amp; Cost Impact
            </CardTitle>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatCard
                label="Sheets of paper"
                value={state.data.summary.sheets_of_paper.toLocaleString()}
              />
              <StatCard
                label="Duplex sheets saved"
                value={state.data.summary.duplex_sheets_saved.toLocaleString()}
              />
              <StatCard
                label="Trees"
                value={state.data.summary.trees_used.toFixed(3)}
              />
              <StatCard
                label="CO₂"
                value={`${(state.data.summary.co2_grams / 1000).toFixed(1)} kg`}
              />
            </div>
            <p className="mt-3 text-xs text-zinc-500">
              Mono cost {formatCurrency(state.data.summary.estimated_cost_mono)}{" "}
              · Color cost{" "}
              {formatCurrency(state.data.summary.estimated_cost_color)} · Paper
              cost {formatCurrency(state.data.summary.estimated_cost_paper)}.
              Toner cost uses each printer&rsquo;s real cartridge cost/yield
              when configured (see the printer&rsquo;s Toner Cartridges panel),
              falling back to the flat rates below otherwise.
            </p>
          </Card>

          <CombinedUsageSection filters={filters} />

          {isAdmin && (
            <SnapshotsSection filters={filters} periodLabel={periodLabel} />
          )}
        </>
      )}
    </div>
  );
}

function SnapshotsSection({
  filters,
  periodLabel,
}: {
  filters: ReportFilters;
  periodLabel: string;
}) {
  const [snapshots, setSnapshots] = useState<ReportSnapshot[] | null>(null);
  const [name, setName] = useState("");
  const [rangeStart, setRangeStart] = useState("");
  const [rangeEnd, setRangeEnd] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function loadSnapshots() {
    listReportSnapshots()
      .then(setSnapshots)
      .catch(() => setSnapshots([]));
  }

  useEffect(loadSnapshots, []);

  async function handleSave() {
    if (!name || !rangeStart || !rangeEnd) {
      setError("Name, start date, and end date are all required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await createReportSnapshot({
        name,
        range_start: rangeStart,
        range_end: rangeEnd,
        period_label: periodLabel,
        filters: {
          building: filters.building ?? null,
          department: filters.department ?? null,
          printer_id: filters.printer_id ?? null,
          submitted_by: filters.submitted_by ?? null,
          status: filters.status ?? null,
          color_mode: filters.color_mode ?? null,
          duplex: filters.duplex ?? null,
        },
      });
      setName("");
      loadSnapshots();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to save snapshot",
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this saved snapshot?")) return;
    await deleteReportSnapshot(id);
    loadSnapshots();
  }

  return (
    <Card className="print:hidden">
      <CardTitle className="mb-3">Saved Snapshots</CardTitle>
      <p className="mb-3 text-xs text-zinc-500">
        Freezes today&rsquo;s totals and fun facts under a name (e.g. a month or
        semester) — the saved numbers stay fixed even if formulas or job data
        change later.
      </p>
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
          Name
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="March 2026"
            className="w-40"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
          Start
          <Input
            type="date"
            value={rangeStart}
            onChange={(e) => setRangeStart(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
          End
          <Input
            type="date"
            value={rangeEnd}
            onChange={(e) => setRangeEnd(e.target.value)}
          />
        </label>
        <Button onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save Snapshot"}
        </Button>
      </div>
      {error && <ErrorState>{error}</ErrorState>}

      {snapshots === null && <Spinner label="Loading snapshots…" />}
      {snapshots !== null && snapshots.length === 0 && (
        <EmptyState>No snapshots saved yet.</EmptyState>
      )}
      {snapshots !== null && snapshots.length > 0 && (
        <div className="flex flex-col gap-2">
          {snapshots.map((snap) => (
            <div
              key={snap.id}
              className="flex items-center justify-between border-t border-black/[.08] pt-2 first:border-t-0 first:pt-0 dark:border-white/[.1]"
            >
              <div>
                <span className="font-medium text-black dark:text-zinc-50">
                  {snap.name}
                </span>
                <span className="ml-2 text-xs text-zinc-500">
                  {snap.range_start} – {snap.range_end}
                </span>
                <div className="text-xs text-zinc-500">
                  {snap.totals.total_pages?.toLocaleString?.() ??
                    snap.totals.total_pages}{" "}
                  pages · {snap.totals.total_jobs} jobs · saved by{" "}
                  {snap.created_by}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone="neutral">
                  {new Date(snap.created_at).toLocaleDateString()}
                </Badge>
                <Button
                  variant="danger"
                  className="!px-3 !py-1 text-xs"
                  onClick={() => handleDelete(snap.id)}
                >
                  Delete
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
