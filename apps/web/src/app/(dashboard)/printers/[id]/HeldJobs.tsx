"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  cancelHeldCupsJob,
  listHeldCupsJobs,
  releaseHeldCupsJob,
  type HeldCupsJob,
  type Printer,
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";

// Jobs CUPS is holding on this printer's queues. Almost always one PrintOps set
// aside because the printer stopped answering both times it was being sent it
// (apps/api/app/printers/crash_guard.py): the rest of the queue carries on, and
// nothing prints this job until somebody decides. Renders nothing when nothing
// is held, which is nearly always.
export function HeldJobsCard({ printer }: { printer: Printer }) {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [jobs, setJobs] = useState<HeldCupsJob[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listHeldCupsJobs(printer.id)
      .then(setJobs)
      .catch(() => setJobs([]));
  }, [printer.id]);

  useEffect(() => {
    if (isAdmin) load();
  }, [isAdmin, load]);

  if (!isAdmin || jobs.length === 0) return null;

  async function act(job: HeldCupsJob, action: "release" | "cancel") {
    if (
      action === "cancel" &&
      !window.confirm(
        `Cancel "${job.document_name ?? `job ${job.cups_job_id}`}"? It will not print, and whoever sent it will need to send it again.`,
      )
    ) {
      return;
    }
    setBusy(job.cups_job_id);
    setError(null);
    try {
      if (action === "release") {
        await releaseHeldCupsJob(printer.id, job.cups_job_id);
      } else {
        await cancelHeldCupsJob(printer.id, job.cups_job_id);
      }
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Could not ${action} the job`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-3">Held Jobs</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        These jobs will not print until they are released. PrintOps holds a job when this
        printer stops answering both times it is being sent it; a printer that keeps failing on
        one document is usually failing on the document itself. Everything else in the queue
        keeps printing. If a released job takes the printer down again, it is held again.
      </p>
      <ul className="divide-y divide-black/[.06] dark:divide-white/[.08]">
        {jobs.map((job) => (
          <li
            key={job.cups_job_id}
            className="flex flex-wrap items-center justify-between gap-3 py-3"
          >
            <div className="min-w-0 text-sm">
              <p className="truncate font-medium text-zinc-900 dark:text-zinc-100">
                {job.document_name ?? `Job ${job.cups_job_id}`}
              </p>
              <p className="text-xs text-zinc-500">
                {[
                  job.owner,
                  job.submitted_at ? `sent ${formatRelativeTime(job.submitted_at)}` : null,
                  job.held_by_printops
                    ? `held by PrintOps ${formatRelativeTime(job.held_at)}`
                    : "on hold",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                disabled={busy !== null}
                onClick={() => act(job, "release")}
              >
                Release
              </Button>
              <Button
                variant="danger"
                disabled={busy !== null}
                onClick={() => act(job, "cancel")}
              >
                Cancel job
              </Button>
            </div>
          </li>
        ))}
      </ul>
      {error && <ErrorState>{error}</ErrorState>}
    </Card>
  );
}
