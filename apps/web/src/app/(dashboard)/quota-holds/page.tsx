"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  listQuotaHolds,
  releaseQuotaHold,
  type Job,
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

export default function QuotaHoldsPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [releasingId, setReleasingId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  useEffect(() => {
    if (currentUser === null) {
      router.replace("/login");
    } else if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  function load() {
    listQuotaHolds()
      .then(setJobs)
      .catch((err: unknown) =>
        setError(
          err instanceof Error ? err.message : "Failed to load quota holds",
        ),
      );
  }

  useEffect(() => {
    if (currentUser?.role === "admin") load();
  }, [currentUser]);

  async function handleRelease(jobId: string) {
    setReleasingId(jobId);
    setRowError(null);
    try {
      await releaseQuotaHold(jobId);
      load();
    } catch (err) {
      setRowError(
        err instanceof ApiError ? err.message : "Failed to release job",
      );
    } finally {
      setReleasingId(null);
    }
  }

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">
            Quota Holds
          </h1>
          <WikiHelpLink page="Quota-Holds" />
        </div>
        <p className="mt-1 text-sm text-zinc-500">
          Jobs held because the submitter was already at or over their page
          quota on that printer. Only an admin can release these — not the
          submitter&rsquo;s own PIN at a release kiosk. Releasing hands the job
          to the printer as-is; it doesn&rsquo;t change or reset anyone&rsquo;s
          quota.
        </p>
      </div>

      {error && <ErrorState>{error}</ErrorState>}
      {rowError && <ErrorState>{rowError}</ErrorState>}
      {jobs === null && !error && <Spinner label="Loading quota holds…" />}
      {jobs !== null && jobs.length === 0 && (
        <EmptyState>
          No jobs are currently held for being over a page quota.
        </EmptyState>
      )}
      {jobs !== null && jobs.length > 0 && (
        <Card className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px] text-left text-sm">
              <thead>
                <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                  <th className="px-4 py-3 font-medium">Submitted By</th>
                  <th className="px-4 py-3 font-medium">Printer</th>
                  <th className="px-4 py-3 font-medium">Document</th>
                  <th className="px-4 py-3 font-medium">Held Since</th>
                  <th className="px-4 py-3 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr
                    key={job.id}
                    className="border-b border-black/[.08] last:border-0 dark:border-white/[.145]"
                  >
                    <td className="px-4 py-3 text-black dark:text-zinc-50">
                      {job.submitted_by_name ?? job.submitted_by ?? "Unknown"}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {job.printer_name}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {job.document_name ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-500">
                      {formatRelativeTime(job.created_at)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Button
                        variant="secondary"
                        className="!px-3 !py-1 text-xs"
                        disabled={releasingId === job.id}
                        onClick={() => handleRelease(job.id)}
                      >
                        {releasingId === job.id ? "Releasing…" : "Release"}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
