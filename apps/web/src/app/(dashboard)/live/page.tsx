"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getLiveHourly, listJobs, type HourlyBucket, type Job } from "@/lib/api";
import { formatBytes, formatRelativeTime } from "@/lib/format";
import { jobStatusInfo } from "@/lib/jobStatus";
import { Badge } from "@/components/ui/Badge";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { StackedVolumeBarChart } from "../insights/charts";

const POLL_INTERVAL_MS = 15 * 1000;
const RECENT_JOBS_LIMIT = 20;

// A true rolling 24h window ending at the top of the current (in-progress)
// hour, not a fixed midnight-to-midnight day — the current hour is always
// the rightmost bar, and as each new hour begins the whole window slides
// forward, aging the oldest hour off the left edge. Rounds `end` up to the
// next clock hour (e.g. 2:37pm -> 3:00pm) so every bucket boundary lines
// up with a real clock hour instead of a "37 minutes past" offset, which
// would make hourLabel below print confusing in-between times.
function rollingWindow(): { start: Date; end: Date } {
  const now = new Date();
  const end = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    now.getHours() + 1,
    0,
    0,
    0,
  );
  const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  return { start, end };
}

function bucketTime(start: Date, hour: number): Date {
  return new Date(start.getTime() + hour * 60 * 60 * 1000);
}

function hourLabel(d: Date): string {
  return d.toLocaleTimeString([], { hour: "numeric" });
}

function dateLabel(d: Date): string {
  return d.toLocaleDateString([], { month: "numeric", day: "numeric" });
}

function ExpandIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4">
      <path
        d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CompressIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4">
      <path
        d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
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
  | { phase: "ok"; buckets: HourlyBucket[]; jobs: Job[]; windowStart: Date }
  | { phase: "error"; message: string };

export default function LiveDashboardPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [now, setNow] = useState<Date>(() => new Date());
  const [isFullscreen, setIsFullscreen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Fullscreens the page's own container (not the whole <body>), so the
  // sidebar nav — a DOM sibling, not a descendant — simply isn't part of
  // what the browser renders in fullscreen. Tracks state off the native
  // fullscreenchange event, not just the button click, so Escape / browser
  // chrome exits still flip the icon back correctly.
  useEffect(() => {
    function onFullscreenChange() {
      setIsFullscreen(document.fullscreenElement === containerRef.current);
    }
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, []);

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      void document.exitFullscreen();
    } else {
      void containerRef.current?.requestFullscreen();
    }
  }

  useEffect(() => {
    let cancelled = false;

    function load() {
      // Computed once per poll and carried on the "ok" state itself
      // (windowStart) rather than recomputed at render time — a second,
      // independent rollingWindow() call a few seconds later could round
      // to a different hour and desync the bar labels from what was
      // actually fetched.
      const { start, end } = rollingWindow();
      Promise.all([getLiveHourly(start, end), listJobs({ limit: RECENT_JOBS_LIMIT })])
        .then(([buckets, jobs]) => {
          if (cancelled) return;
          setState({ phase: "ok", buckets, jobs, windowStart: start });
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

  // Separate 1s ticker just for the "last updated"/countdown display —
  // decoupled from the 15s data-poll interval above so the countdown
  // itself is smooth instead of only updating in 15s jumps.
  useEffect(() => {
    const tick = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(tick);
  }, []);

  const secondsUntilRefresh = lastUpdated
    ? Math.max(0, Math.ceil((lastUpdated.getTime() + POLL_INTERVAL_MS - now.getTime()) / 1000))
    : null;

  const chartData =
    state.phase === "ok"
      ? state.buckets.map((b, i) => {
          const d = bucketTime(state.windowStart, b.hour);
          const prevD =
            i > 0 ? bucketTime(state.windowStart, state.buckets[i - 1].hour) : null;
          // A rolling 24h window almost always crosses one calendar date —
          // label the date only on the bar where it changes (and the very
          // first bar), instead of on every bar, so the axis stays
          // readable while still orienting "yesterday" vs "today".
          const isNewDay = !prevD || d.toDateString() !== prevD.toDateString();
          return {
            label: isNewDay ? `${dateLabel(d)} ${hourLabel(d)}` : hourLabel(d),
            print: b.total_pages,
            copy: b.copy_pages,
          };
        })
      : [];

  const totals =
    state.phase === "ok"
      ? state.buckets.reduce(
          (acc, b) => ({
            jobs: acc.jobs + b.job_count,
            pages: acc.pages + b.total_pages,
            color: acc.color + b.color_pages,
            duplex: acc.duplex + b.duplex_pages,
            copyPages: acc.copyPages + b.copy_pages,
          }),
          { jobs: 0, pages: 0, color: 0, duplex: 0, copyPages: 0 },
        )
      : null;

  return (
    <div
      ref={containerRef}
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 bg-white p-0 dark:bg-zinc-950 [&:fullscreen]:max-w-none [&:fullscreen]:overflow-y-auto [&:fullscreen]:p-6"
    >
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Live Dashboard</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Rolling last 24 hours — updates automatically every 15 seconds.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastUpdated && (
            <span className="flex items-center gap-2 text-xs text-zinc-400">
              <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
              Updated {lastUpdated.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}
              {secondsUntilRefresh !== null && ` · next in ${secondsUntilRefresh}s`}
            </span>
          )}
          <button
            type="button"
            onClick={toggleFullscreen}
            title={isFullscreen ? "Exit full screen" : "Full screen"}
            aria-label={isFullscreen ? "Exit full screen" : "Full screen"}
            className="flex h-8 w-8 items-center justify-center rounded-md border border-black/[.1] text-zinc-500 hover:bg-black/[.04] dark:border-white/[.15] dark:text-zinc-400 dark:hover:bg-white/[.08]"
          >
            {isFullscreen ? <CompressIcon /> : <ExpandIcon />}
          </button>
        </div>
      </div>

      {state.phase === "loading" && <Spinner label="Loading live dashboard…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && totals && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            <StatTile label="Jobs (24h)" value={totals.jobs.toLocaleString()} />
            <StatTile label="Pages (24h)" value={totals.pages.toLocaleString()} />
            <StatTile label="Copy Pages (24h)" value={totals.copyPages.toLocaleString()} />
            <StatTile label="Color Pages" value={totals.color.toLocaleString()} />
            <StatTile label="Duplex Pages" value={totals.duplex.toLocaleString()} />
          </div>

          <Card>
            <CardTitle className="mb-4">Pages by Hour (Last 24h)</CardTitle>
            <StackedVolumeBarChart data={chartData} />
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
                          {job.submitted_by_name ?? job.submitted_by ?? "Unknown"}
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
