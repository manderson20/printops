"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ApiError,
  adminDiscardHeldJob,
  adminReleaseHeldJob,
  cancelJob,
  listAllHeldJobs,
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
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; jobs: Job[] }
  | { phase: "error"; message: string };

type SortKey = "printer" | "status" | "submitted";

// Why a job is waiting, in the words an admin would use. Quota is the one
// that is deliberately theirs to release; the others are ordinarily released
// by the person who sent them, at the printer.
const HOLD_LABEL: Record<string, string> = {
  quota: "Over quota",
  pin_release: "Release at printer",
  follow_me: "Follow-Me",
  printer_offline: "Waiting for printer",
};

const HOLD_TONE: Record<string, "warning" | "info" | "neutral"> = {
  quota: "warning",
  pin_release: "info",
  follow_me: "info",
  // Not a warning: nothing is wrong with the job, and it goes on its own as
  // soon as the printer answers. Releasing it by hand is possible but not
  // something anyone needs to do.
  printer_offline: "neutral",
};

const SELECT_CLASS =
  "rounded-lg border border-black/[.15] bg-white px-2 py-1 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50";

// A "forwarding" job stuck this long probably means the backend crashed or
// the printer is jammed/unreachable — flagged in the UI as a nudge to check
// the printer and consider cancelling, not an automatic timeout.
//
// The server does resolve these now, but not on this timescale and not on a
// guess: app/printers/job_reconcile.py waits half an hour before it asks CUPS
// about a job at all, and leaves alone any job CUPS says is still printing —
// which a big job legitimately is, for hours. The badge stays as the earlier,
// weaker signal it always was, hence the question mark.
const STUCK_THRESHOLD_MS = 10 * 60 * 1000;

function JobsList() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const printerId = searchParams.get("printer_id") ?? "";
  // Seeded from the URL so the retired /held-jobs route can redirect here
  // with ?status=held and land on the same list it used to be.
  const [statusFilter, setStatusFilter] = useState<JobStatus | "">(
    (searchParams.get("status") as JobStatus | "") ?? "",
  );
  const isAdmin = useCurrentUser()?.role === "admin";

  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [printers, setPrinters] = useState<Printer[]>([]);
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({
    key: "submitted",
    dir: "desc",
  });
  const [cancelling, setCancelling] = useState<Record<string, boolean>>({});
  const [cancelErrors, setCancelErrors] = useState<Record<string, string>>({});
  // Held-job state, from the Held Jobs page this absorbs.
  const [busyHold, setBusyHold] = useState<Record<string, string>>({});
  const [holdErrors, setHoldErrors] = useState<Record<string, string>>({});
  const [target, setTarget] = useState<Record<string, string>>({});

  // A Follow-Me job was addressed to a virtual queue with no device behind
  // it. The kiosk resolves that to whichever printer the person is standing
  // at; an admin is not standing anywhere, so they have to say.
  // Archived is excluded deliberately: this list is loaded with
  // includeArchived so the *filter* can still name a retired printer, but
  // archiving tears down the CUPS queue. Releasing to one fails with an
  // unknown destination, and a failed release flips the job out of "held" —
  // taking Release and Discard with it, so the hold cannot be retried.
  const followMePrinters = useMemo(
    () =>
      printers.filter(
        (p) => p.follow_me_enabled && !p.is_virtual && !p.archived_at,
      ),
    [printers],
  );

  useEffect(() => {
    listPrinters({ includeArchived: true })
      .then(setPrinters)
      .catch(() => setPrinters([]));
  }, []);

  useEffect(() => {
    setState({ phase: "loading" });
    // Holds come from their own uncapped endpoint rather than the newest 200
    // jobs. A hold waits — that is what makes it a hold — so on a busy fleet
    // it drifts out of any recent-jobs window and becomes unreleasable, which
    // is exactly the job someone is looking for. The general list stays
    // capped; it is a log, and nobody needs its tail.
    const load =
      statusFilter === "held"
        ? listAllHeldJobs()
        : listJobs({ printer_id: printerId || undefined, limit: 200 });
    load
      .then((jobs) => setState({ phase: "ok", jobs }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load jobs",
        }),
      );
  }, [printerId, statusFilter]);

  function reload() {
    const load =
      statusFilter === "held"
        ? listAllHeldJobs()
        : listJobs({ printer_id: printerId || undefined, limit: 200 });
    load.then((jobs) => setState({ phase: "ok", jobs })).catch(() => undefined);
  }

  async function handleRelease(job: Job) {
    setBusyHold((prev) => ({ ...prev, [job.id]: "releasing" }));
    setHoldErrors((prev) => ({ ...prev, [job.id]: "" }));
    try {
      await adminReleaseHeldJob(job.id, target[job.id] || undefined);
      reload();
    } catch (err) {
      setHoldErrors((prev) => ({
        ...prev,
        [job.id]: err instanceof ApiError ? err.message : "Failed to release job",
      }));
    } finally {
      setBusyHold((prev) => ({ ...prev, [job.id]: "" }));
    }
  }

  async function handleDiscard(job: Job) {
    // Named, not a bare "are you sure": this destroys someone's queued
    // document, and the person confirming is not the person who sent it.
    const who = job.submitted_by ?? "someone";
    const what = job.document_name ?? "this document";
    if (
      !confirm(
        `Discard "${what}" from ${who} without printing it?\n\n` +
          "The document is deleted and cannot be recovered. The job stays in " +
          "the history and reports, marked cancelled.",
      )
    ) {
      return;
    }
    setBusyHold((prev) => ({ ...prev, [job.id]: "discarding" }));
    setHoldErrors((prev) => ({ ...prev, [job.id]: "" }));
    try {
      await adminDiscardHeldJob(job.id);
      reload();
    } catch (err) {
      setHoldErrors((prev) => ({
        ...prev,
        [job.id]: err instanceof ApiError ? err.message : "Failed to discard job",
      }));
    } finally {
      setBusyHold((prev) => ({ ...prev, [job.id]: "" }));
    }
  }

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
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Jobs</h1>
          <WikiHelpLink page="Jobs" />
        </div>
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
            <option value="held">Held</option>
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

      {statusFilter === "held" && isAdmin && (
        <p className="mb-4 max-w-4xl text-sm text-zinc-500">
          A quota hold has always been an admin&rsquo;s to release — the
          submitter&rsquo;s own PIN can&rsquo;t. A print-release hold is
          normally released by whoever sent it, at the printer; releasing it
          here is for when they can&rsquo;t, because their PIN isn&rsquo;t set
          up yet or the address on the job doesn&rsquo;t match a person — which
          is the case for a test page sent from this server. Releasing hands the
          job to the printer as-is; it doesn&rsquo;t change or reset
          anyone&rsquo;s quota. Discarding deletes the document for good, but
          keeps the job in the history and reports.
        </p>
      )}

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
                        {job.submitted_by_name ?? job.submitted_by ?? "—"}
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
                          {/* Only when the reason changes who is expected to
                              act. A print-release hold is what "Held" already
                              means around here — nearly every hold on this
                              server is one — so labelling it repeats the
                              status badge next to it. The others genuinely
                              differ: a quota hold is an admin's to release,
                              and an offline hold needs nobody at all. */}
                          {job.status === "held" &&
                            job.hold_reason &&
                            job.hold_reason !== "pin_release" && (
                              <Badge tone={HOLD_TONE[job.hold_reason] ?? "neutral"}>
                                {HOLD_LABEL[job.hold_reason] ?? "Held"}
                              </Badge>
                            )}
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
                        {/* Not just in-flight jobs. A job PrintOps has marked
                            failed is often still sitting on the CUPS queue
                            being retried — and retried is exactly how a bad
                            job stops a printer over and over. Everything
                            except a job that already printed (and held jobs,
                            which are discarded below) can be cancelled; the
                            API says so plainly if there was nothing left on
                            the print server to cancel. */}
                        {job.status !== "forwarded" && job.status !== "held" && (
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
                        {job.status === "held" && (
                          <div className="flex flex-col items-start gap-1">
                            <div className="flex items-center gap-2">
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
                                  !!busyHold[job.id] ||
                                  (job.hold_reason === "follow_me" && !target[job.id])
                                }
                                onClick={() => handleRelease(job)}
                              >
                                {busyHold[job.id] === "releasing" ? "Releasing…" : "Release"}
                              </Button>
                              {/* Destroys the document. Kept visually quieter
                                  than Release so the printing action stays the
                                  obvious one. */}
                              <Button
                                variant="danger"
                                className="!px-3 !py-1 text-xs"
                                disabled={!!busyHold[job.id]}
                                onClick={() => handleDiscard(job)}
                              >
                                {busyHold[job.id] === "discarding" ? "Discarding…" : "Discard"}
                              </Button>
                            </div>
                            {holdErrors[job.id] && (
                              <span className="max-w-[16rem] text-xs text-red-600 dark:text-red-400">
                                {holdErrors[job.id]}
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
