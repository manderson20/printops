"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  adminReleaseHeldJob,
  listAllHeldJobs,
  listPrinters,
  type Job,
  type Printer,
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

// Why a job is waiting, in the words an admin would use. Quota is the one
// that is deliberately theirs to release; the others are ordinarily released
// by the person who sent them, at the printer.
const HOLD_LABEL: Record<string, string> = {
  quota: "Over quota",
  pin_release: "Release at printer",
  follow_me: "Follow-Me",
};

const HOLD_TONE: Record<string, "warning" | "info" | "neutral"> = {
  quota: "warning",
  pin_release: "info",
  follow_me: "info",
};

export default function HeldJobsPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [releasingId, setReleasingId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  // A Follow-Me job was addressed to a virtual queue with no device behind it,
  // so releasing one means choosing where it comes out. Everything else
  // defaults to the printer it was sent to and needs no picker.
  const [followMePrinters, setFollowMePrinters] = useState<Printer[]>([]);
  const [target, setTarget] = useState<Record<string, string>>({});

  useEffect(() => {
    if (currentUser === null) {
      router.replace("/login");
    } else if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  function load() {
    listAllHeldJobs()
      .then(setJobs)
      .catch((err: unknown) =>
        setError(
          err instanceof Error ? err.message : "Failed to load held jobs",
        ),
      );
  }

  useEffect(() => {
    if (currentUser?.role === "admin") load();
  }, [currentUser]);

  useEffect(() => {
    if (currentUser?.role !== "admin") return;
    listPrinters()
      .then((printers) =>
        setFollowMePrinters(
          printers.filter((p) => p.follow_me_enabled && !p.is_virtual),
        ),
      )
      .catch(() => setFollowMePrinters([]));
  }, [currentUser]);

  async function handleRelease(jobId: string) {
    setReleasingId(jobId);
    setRowError(null);
    try {
      await adminReleaseHeldJob(jobId, target[jobId]);
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
            Held Jobs
          </h1>
          <WikiHelpLink page="Held-Jobs" />
        </div>
        <p className="mt-1 text-sm text-zinc-500">
          Every job PrintOps is currently holding. A quota hold has always been
          an admin&rsquo;s to release — the submitter&rsquo;s own PIN
          can&rsquo;t. A print-release hold is normally released by whoever sent
          it, at the printer; releasing it here is for when they can&rsquo;t,
          because their PIN isn&rsquo;t set up yet or the address on the job
          doesn&rsquo;t match a person &mdash; which is the case for a test page
          sent from this server. Releasing hands the job to the printer as-is;
          it doesn&rsquo;t change or reset anyone&rsquo;s quota.
        </p>
      </div>

      {error && <ErrorState>{error}</ErrorState>}
      {rowError && <ErrorState>{rowError}</ErrorState>}
      {jobs === null && !error && <Spinner label="Loading held jobs…" />}
      {jobs !== null && jobs.length === 0 && (
        <EmptyState>No jobs are currently held.</EmptyState>
      )}
      {jobs !== null && jobs.length > 0 && (
        <Card className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px] text-left text-sm">
              <thead>
                <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                  <th className="px-4 py-3 font-medium">Submitted By</th>
                  <th className="px-4 py-3 font-medium">Printer</th>
                  <th className="px-4 py-3 font-medium">Held For</th>
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
                    <td className="px-4 py-3">
                      <Badge
                        tone={HOLD_TONE[job.hold_reason ?? ""] ?? "neutral"}
                      >
                        {HOLD_LABEL[job.hold_reason ?? ""] ?? "Held"}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {job.document_name ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-500">
                      {formatRelativeTime(job.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-2">
                        {job.hold_reason === "follow_me" && (
                          <select
                            className="rounded-md border border-black/[.08] bg-transparent px-2 py-1 text-xs dark:border-white/[.145]"
                            value={target[job.id] ?? ""}
                            onChange={(e) =>
                              setTarget((prev) => ({
                                ...prev,
                                [job.id]: e.target.value,
                              }))
                            }
                          >
                            <option value="">Release at…</option>
                            {followMePrinters.map((printer) => (
                              <option key={printer.id} value={printer.id}>
                                {printer.name}
                              </option>
                            ))}
                          </select>
                        )}
                        <Button
                          variant="secondary"
                          className="!px-3 !py-1 text-xs"
                          disabled={
                            releasingId === job.id ||
                            (job.hold_reason === "follow_me" && !target[job.id])
                          }
                          onClick={() => handleRelease(job.id)}
                        >
                          {releasingId === job.id ? "Releasing…" : "Release"}
                        </Button>
                      </div>
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
