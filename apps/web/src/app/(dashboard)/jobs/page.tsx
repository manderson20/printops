"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ApiError,
  cancelJob,
  listJobs,
  listPrinters,
  type Job,
  type JobStatus,
  type Printer,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { attributionMethodInfo, jobStatusInfo } from "@/lib/jobStatus";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; jobs: Job[] }
  | { phase: "error"; message: string };

type SortKey = "printer" | "status" | "submitted";

const SELECT_CLASS =
  "rounded-lg border border-black/[.15] bg-white px-2 py-1 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50";

// A "forwarding" job stuck this long probably means the backend crashed or
// the printer is jammed/unreachable — flagged in the UI as a nudge to check
// the printer and consider cancelling, not an automatic timeout.
const STUCK_THRESHOLD_MS = 10 * 60 * 1000;

function JobsList() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const printerId = searchParams.get("printer_id") ?? "";
  const isAdmin = useCurrentUser()?.role === "admin";

  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [printers, setPrinters] = useState<Printer[]>([]);
  const [statusFilter, setStatusFilter] = useState<JobStatus | "">("");
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({
    key: "submitted",
    dir: "desc",
  });
  const [cancelling, setCancelling] = useState<Record<string, boolean>>({});
  const [cancelErrors, setCancelErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    listPrinters({ includeArchived: true })
      .then(setPrinters)
      .catch(() => setPrinters([]));
  }, []);

  useEffect(() => {
    setState({ phase: "loading" });
    listJobs({ printer_id: printerId || undefined, limit: 200 })
      .then((jobs) => setState({ phase: "ok", jobs }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load jobs",
        }),
      );
  }, [printerId]);

  function handlePrinterFilterChange(value: string) {
    const params = new URLSearchParams(searchParams);
    if (value) {
      params.set("printer_id", value);
    } else {
      params.delete("printer_id");
    }
    const qs = params.toString();
    router.push(qs ? `/jobs?${qs}` : "/jobs");
  }

  function toggleSort(key: SortKey) {
    setSort((prev) =>
      prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" },
    );
  }

  async function handleCancel(job: Job) {
    setCancelling((prev) => ({ ...prev, [job.id]: true }));
    setCancelErrors((prev) => {
      const next = { ...prev };
      delete next[job.id];
      return next;
    });
    try {
      const updated = await cancelJob(job.id);
      setState((prev) =>
        prev.phase === "ok"
          ? { phase: "ok", jobs: prev.jobs.map((j) => (j.id === updated.id ? updated : j)) }
          : prev,
      );
    } catch (err) {
      setCancelErrors((prev) => ({
        ...prev,
        [job.id]: err instanceof ApiError ? err.message : "Cancel failed",
      }));
    } finally {
      setCancelling((prev) => ({ ...prev, [job.id]: false }));
    }
  }

  const visibleJobs = useMemo(() => {
    if (state.phase !== "ok") return [];
    let jobs = state.jobs;
    if (statusFilter) {
      jobs = jobs.filter((job) => job.status === statusFilter);
    }
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...jobs].sort((a, b) => {
      switch (sort.key) {
        case "printer":
          return a.printer_name.localeCompare(b.printer_name) * dir;
        case "status":
          return a.status.localeCompare(b.status) * dir;
        case "submitted":
        default:
          return (new Date(a.created_at).getTime() - new Date(b.created_at).getTime()) * dir;
      }
    });
  }, [state, statusFilter, sort]);

  function sortIndicator(key: SortKey) {
    if (sort.key !== key) return null;
    return sort.dir === "asc" ? " ▲" : " ▼";
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Jobs</h1>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
          Printer
          <select
            value={printerId}
            onChange={(e) => handlePrinterFilterChange(e.target.value)}
            className={SELECT_CLASS}
          >
            <option value="">All printers</option>
            {printers.map((printer) => (
              <option key={printer.id} value={printer.id}>
                {printer.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
          Status
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as JobStatus | "")}
            className={SELECT_CLASS}
          >
            <option value="">All statuses</option>
            <option value="forwarding">Forwarding</option>
            <option value="forwarded">Forwarded</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </label>
        {printerId && (
          <button
            onClick={() => handlePrinterFilterChange("")}
            className="text-sm font-medium text-accent hover:underline"
          >
            Clear printer filter
          </button>
        )}
      </div>

      {state.phase === "loading" && <Spinner label="Loading jobs…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}
      {state.phase === "ok" && visibleJobs.length === 0 && (
        <EmptyState>
          {printerId || statusFilter ? "No jobs match these filters." : "No jobs logged yet."}
        </EmptyState>
      )}
      {state.phase === "ok" && visibleJobs.length > 0 && (
        <Card className="overflow-hidden p-0">
          <table className="w-full text-left text-sm">
            <thead className="bg-black/[.03] text-zinc-600 dark:bg-white/[.05] dark:text-zinc-400">
              <tr>
                <th className="cursor-pointer select-none px-4 py-3 font-medium" onClick={() => toggleSort("printer")}>
                  Printer{sortIndicator("printer")}
                </th>
                <th className="px-4 py-3 font-medium">Document</th>
                <th className="px-4 py-3 font-medium">Submitted By</th>
                <th className="px-4 py-3 font-medium">Device</th>
                <th className="cursor-pointer select-none px-4 py-3 font-medium" onClick={() => toggleSort("status")}>
                  Status{sortIndicator("status")}
                </th>
                <th className="px-4 py-3 font-medium">Pages</th>
                <th className="px-4 py-3 font-medium">Size</th>
                <th className="cursor-pointer select-none px-4 py-3 font-medium" onClick={() => toggleSort("submitted")}>
                  Submitted{sortIndicator("submitted")}
                </th>
                {isAdmin && <th className="px-4 py-3 font-medium">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {visibleJobs.map((job) => {
                const info = jobStatusInfo(job.status);
                const attribution = attributionMethodInfo(job.attribution_method);
                const isStuck =
                  job.status === "forwarding" &&
                  Date.now() - new Date(job.created_at).getTime() > STUCK_THRESHOLD_MS;
                return (
                  <tr
                    key={job.id}
                    className="border-t border-black/[.08] hover:bg-black/[.02] dark:border-white/[.1] dark:hover:bg-white/[.03]"
                  >
                    <td className="px-4 py-3">
                      <Link
                        href={`/printers/${job.printer_id}`}
                        className="font-medium text-black hover:underline dark:text-zinc-50"
                      >
                        {job.printer_name}
                      </Link>
                    </td>
                    <td className="max-w-[16rem] truncate px-4 py-3 text-zinc-600 dark:text-zinc-400" title={job.document_name ?? undefined}>
                      {job.document_name ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      <div className="flex items-center gap-2">
                        {job.submitted_by ?? "—"}
                        <Badge tone={attribution.tone}>{attribution.label}</Badge>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {job.device_name ?? "—"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-2">
                          <Badge tone={info.tone}>{info.label}</Badge>
                          {isStuck && <Badge tone="warning">Stuck?</Badge>}
                        </div>
                        {job.status === "failed" && job.error_message && (
                          <span className="text-xs text-red-600 dark:text-red-400">
                            {job.error_message}
                          </span>
                        )}
                        {job.status === "cancelled" && job.error_message && (
                          <span className="text-xs text-zinc-400">{job.error_message}</span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {job.page_count ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {formatBytes(job.file_size_bytes)}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {new Date(job.created_at).toLocaleString()}
                    </td>
                    {isAdmin && (
                      <td className="px-4 py-3">
                        {job.status === "forwarding" && (
                          <div className="flex flex-col items-start gap-1">
                            <Button
                              variant="danger"
                              className="!px-3 !py-1 text-xs"
                              disabled={cancelling[job.id]}
                              onClick={() => handleCancel(job)}
                            >
                              {cancelling[job.id] ? "Cancelling…" : "Cancel"}
                            </Button>
                            {cancelErrors[job.id] && (
                              <span className="max-w-[16rem] text-xs text-red-600 dark:text-red-400">
                                {cancelErrors[job.id]}
                              </span>
                            )}
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

export default function JobsPage() {
  return (
    <Suspense fallback={<Spinner label="Loading jobs…" />}>
      <JobsList />
    </Suspense>
  );
}
