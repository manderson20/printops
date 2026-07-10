"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getLiveHourly, listJobs, type HourlyBucket, type Job } from "@/lib/api";
import { formatBytes, formatRelativeTime } from "@/lib/format";
import { jobStatusInfo } from "@/lib/jobStatus";
import { Badge } from "@/components/ui/Badge";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { VolumeBarChart } from "../insights/charts";

const POLL_INTERVAL_MS = 15 * 1000;
const RECENT_JOBS_LIMIT = 20;

function todayWindow(): { start: Date; end: Date } {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000);
  return { start, end };
}

function hourLabel(start: Date, hour: number): string {
  const d = new Date(start.getTime() + hour * 60 * 60 * 1000);
  return d.toLocaleTimeString([], { hour: "numeric" });
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-zinc-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-black dark:text-zinc-50">{value}</p>
    </Card>
  );
}

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; buckets: HourlyBucket[]; jobs: Job[] }
  | { phase: "error"; message: string };

export default function LiveDashboardPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;

    function load() {
      const { start, end } = todayWindow();
      Promise.all([getLiveHourly(start, end), listJobs({ limit: RECENT_JOBS_LIMIT })])
        .then(([buckets, jobs]) => {
          if (cancelled) return;
          setState({ phase: "ok", buckets, jobs });
          setLastUpdated(new Date());
        })
        .catch((error: unknown) => {
          if (cancelled) return;
          setState({
            phase: "error",
            message: error instanceof Error ? error.message : "Failed to load live dashboard",
          });
        });
    }

    load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const { start } = todayWindow();
  const chartData =
    state.phase === "ok"
      ? state.buckets.map((b) => ({ label: hourLabel(start, b.hour), value: b.total_pages }))
      : [];

  const totals =
    state.phase === "ok"
      ? state.buckets.reduce(
          (acc, b) => ({
            jobs: acc.jobs + b.job_count,
            pages: acc.pages + b.total_pages,
            color: acc.color + b.color_pages,
            duplex: acc.duplex + b.duplex_pages,
          }),
          { jobs: 0, pages: 0, color: 0, duplex: 0 },
        )
      : null;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Live Dashboard</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Today&rsquo;s print activity by hour — updates on its own every 15 seconds, no refresh
            needed. Good for leaving up on a TV display.
          </p>
        </div>
        {lastUpdated && (
          <span className="flex items-center gap-2 text-xs text-zinc-400">
            <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
            Updated {formatRelativeTime(lastUpdated.toISOString())}
          </span>
        )}
      </div>

      {state.phase === "loading" && <Spinner label="Loading live dashboard…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && totals && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatTile label="Jobs Today" value={totals.jobs.toLocaleString()} />
            <StatTile label="Pages Today" value={totals.pages.toLocaleString()} />
            <StatTile label="Color Pages" value={totals.color.toLocaleString()} />
            <StatTile label="Duplex Pages" value={totals.duplex.toLocaleString()} />
          </div>

          <Card>
            <CardTitle className="mb-4">Pages by Hour</CardTitle>
            <VolumeBarChart data={chartData} />
          </Card>

          <Card className="overflow-hidden p-0">
            <div className="flex items-center justify-between p-4">
              <CardTitle>Recent Print Jobs</CardTitle>
              <Link href="/jobs" className="text-xs font-medium text-accent hover:underline">
                View all
              </Link>
            </div>
            {state.jobs.length === 0 ? (
              <div className="p-6 pt-0">
                <EmptyState>No jobs logged yet today.</EmptyState>
              </div>
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="bg-black/[.03] text-zinc-600 dark:bg-white/[.05] dark:text-zinc-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">User</th>
                    <th className="px-4 py-3 font-medium">Printer</th>
                    <th className="px-4 py-3 font-medium">Pages</th>
                    <th className="px-4 py-3 font-medium">Color</th>
                    <th className="px-4 py-3 font-medium">Sides</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Size</th>
                    <th className="px-4 py-3 font-medium">Submitted</th>
                  </tr>
                </thead>
                <tbody>
                  {state.jobs.map((job) => {
                    const info = jobStatusInfo(job.status);
                    return (
                      <tr
                        key={job.id}
                        className="border-t border-black/[.08] dark:border-white/[.1]"
                      >
                        <td className="px-4 py-3 text-zinc-700 dark:text-zinc-300">
                          {job.submitted_by ?? "Unknown"}
                        </td>
                        <td className="px-4 py-3">
                          <Link
                            href={`/printers/${job.printer_id}`}
                            className="font-medium text-black hover:underline dark:text-zinc-50"
                          >
                            {job.printer_name}
                          </Link>
                        </td>
                        <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                          {job.page_count ?? "—"}
                        </td>
                        <td className="px-4 py-3">
                          {job.color_mode === "color" ? (
                            <Badge tone="info">Color</Badge>
                          ) : job.color_mode === "monochrome" ? (
                            <Badge tone="neutral">Mono</Badge>
                          ) : (
                            <span className="text-zinc-400">—</span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          {job.duplex === true ? (
                            <Badge tone="neutral">Duplex</Badge>
                          ) : job.duplex === false ? (
                            <Badge tone="neutral">Simplex</Badge>
                          ) : (
                            <span className="text-zinc-400">—</span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <Badge tone={info.tone}>{info.label}</Badge>
                        </td>
                        <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                          {formatBytes(job.file_size_bytes)}
                        </td>
                        <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                          {formatRelativeTime(job.created_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
