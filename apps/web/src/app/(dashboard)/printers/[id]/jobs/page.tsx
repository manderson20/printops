"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, listJobs, purgePrinterJobs, type Job } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { attributionMethodInfo, jobStatusInfo } from "@/lib/jobStatus";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { usePrinterDetail } from "../PrinterDetailContext";

export default function PrinterJobsTab() {
  const { printer } = usePrinterDetail();
  const isAdmin = useCurrentUser()?.role === "admin";
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [purging, setPurging] = useState(false);
  const [purgeResult, setPurgeResult] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    listJobs({ printer_id: printer.id, limit: 5 })
      .then(setJobs)
      .catch(() => setJobs([]));
  }, [printer.id]);

  async function handlePurgeQueue() {
    if (!confirm("Cancel every job queued on this printer? This can't be undone.")) return;
    setPurging(true);
    setActionError(null);
    setPurgeResult(null);
    try {
      const result = await purgePrinterJobs(printer.id);
      setPurgeResult(
        result.cancelled_count === 0
          ? "No PrintOps-tracked jobs were pending — the CUPS queue has been cleared."
          : `Cancelled ${result.cancelled_count} pending job${result.cancelled_count === 1 ? "" : "s"}.`,
      );
      const refreshed = await listJobs({ printer_id: printer.id, limit: 5 }).catch(() => null);
      if (refreshed) setJobs(refreshed);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Queue purge failed");
    } finally {
      setPurging(false);
    }
  }

  return (
    <Card>
      <div className="mb-4 flex items-center justify-between">
        <CardTitle>Recent Jobs</CardTitle>
        <div className="flex items-center gap-3">
          {isAdmin && (
            <Button
              variant="danger"
              className="!px-3 !py-1 text-xs"
              onClick={handlePurgeQueue}
              disabled={purging}
            >
              {purging ? "Purging…" : "Purge Queue"}
            </Button>
          )}
          <Link
            href={`/jobs?printer_id=${printer.id}`}
            className="text-xs font-medium text-accent hover:underline"
          >
            View all
          </Link>
        </div>
      </div>

      {actionError && <ErrorState>{actionError}</ErrorState>}
      {purgeResult && (
        <p className="mb-3 text-xs text-emerald-700 dark:text-emerald-400">{purgeResult}</p>
      )}

      {jobs === null && <Spinner label="Loading jobs…" />}
      {jobs !== null && jobs.length === 0 && (
        <EmptyState>No jobs logged for this printer yet.</EmptyState>
      )}
      {jobs !== null && jobs.length > 0 && (
        <div className="flex flex-col gap-2 text-sm">
          {jobs.map((job) => {
            const info = jobStatusInfo(job.status);
            const attribution = attributionMethodInfo(job.attribution_method);
            return (
              <div
                key={job.id}
                className="flex items-center justify-between border-t border-black/[.08] pt-2 first:border-t-0 first:pt-0 dark:border-white/[.1]"
              >
                <div className="flex flex-col">
                  <span className="flex items-center gap-2 text-zinc-700 dark:text-zinc-300">
                    {job.submitted_by_name ?? job.submitted_by ?? "Unknown user"}
                    <Badge tone={attribution.tone}>{attribution.label}</Badge>
                  </span>
                  <span className="text-xs text-zinc-400">
                    {new Date(job.created_at).toLocaleString()} · {formatBytes(job.file_size_bytes)}
                  </span>
                </div>
                <Badge tone={info.tone}>{info.label}</Badge>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
