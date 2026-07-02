"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { listJobs, type Job } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { attributionMethodInfo, jobStatusInfo } from "@/lib/jobStatus";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; jobs: Job[] }
  | { phase: "error"; message: string };

function JobsList() {
  const searchParams = useSearchParams();
  const printerId = searchParams.get("printer_id") ?? undefined;
  const [state, setState] = useState<LoadState>({ phase: "loading" });

  useEffect(() => {
    setState({ phase: "loading" });
    listJobs({ printer_id: printerId, limit: 100 })
      .then((jobs) => setState({ phase: "ok", jobs }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load jobs",
        }),
      );
  }, [printerId]);

  return (
    <div className="flex w-full max-w-5xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Jobs</h1>
        {printerId && (
          <Link href="/jobs" className="text-sm font-medium text-accent hover:underline">
            Clear printer filter
          </Link>
        )}
      </div>

      {state.phase === "loading" && <Spinner label="Loading jobs…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}
      {state.phase === "ok" && state.jobs.length === 0 && (
        <EmptyState>
          {printerId ? "No jobs logged for this printer yet." : "No jobs logged yet."}
        </EmptyState>
      )}
      {state.phase === "ok" && state.jobs.length > 0 && (
        <Card className="overflow-hidden p-0">
          <table className="w-full text-left text-sm">
            <thead className="bg-black/[.03] text-zinc-600 dark:bg-white/[.05] dark:text-zinc-400">
              <tr>
                <th className="px-4 py-3 font-medium">Printer</th>
                <th className="px-4 py-3 font-medium">Submitted By</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Size</th>
                <th className="px-4 py-3 font-medium">Submitted</th>
              </tr>
            </thead>
            <tbody>
              {state.jobs.map((job) => {
                const info = jobStatusInfo(job.status);
                const attribution = attributionMethodInfo(job.attribution_method);
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
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      <div className="flex items-center gap-2">
                        {job.submitted_by ?? "—"}
                        <Badge tone={attribution.tone}>{attribution.label}</Badge>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col gap-1">
                        <Badge tone={info.tone}>{info.label}</Badge>
                        {job.status === "failed" && job.error_message && (
                          <span className="text-xs text-red-600 dark:text-red-400">
                            {job.error_message}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {formatBytes(job.file_size_bytes)}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {new Date(job.created_at).toLocaleString()}
                    </td>
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
