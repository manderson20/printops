"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  getMyPrintQueue,
  restorePrintQueueJob,
  yieldPrintQueueJob,
  type PrintQueueHeldJob,
  type PrintQueueJob,
  type PrintQueueView,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { formatBytes } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; view: PrintQueueView }
  | { phase: "error"; message: string };

// The queue moves on its own — a job finishes, someone else sends one — so a
// page showing a position that silently went stale would be worse than no
// page. Short enough to feel live while someone stands at the printer.
const REFRESH_MS = 10_000;

function jobLabel(job: PrintQueueJob): string {
  if (!job.mine) return "Another staff member";
  return job.document_name ?? "Your job";
}

function heldBadge(job: PrintQueueHeldJob) {
  if (job.reason === "printer_offline") return <Badge tone="warning">Printer is off</Badge>;
  if (job.reason === "quota") return <Badge tone="warning">Over your quota</Badge>;
  if (job.reason === "pin_release" || job.reason === "follow_me")
    return <Badge tone="info">Waiting for you at the printer</Badge>;
  return <Badge tone="neutral">Held</Badge>;
}

function heldExplanation(job: PrintQueueHeldJob): string {
  const expiry = job.expires_at
    ? ` If it isn't printed by ${new Date(job.expires_at).toLocaleString()}, it is deleted unprinted.`
    : "";
  switch (job.reason) {
    case "printer_offline":
      // Deliberately reassuring and specific: this is the case that looks like
      // a job disappeared, and the honest answer is that nothing is needed.
      return (
        `${job.printer_name} was switched off or unreachable when you sent this, so PrintOps ` +
        `is keeping it. It prints by itself when the printer is back on — you don't need to ` +
        `send it again.` + expiry
      );
    case "quota":
      return (
        "This would take you past your print quota, so it needs an administrator to release " +
        "it. It has not been printed and has not counted against anyone." + expiry
      );
    case "pin_release":
      return `Go to ${job.printer_name} and enter your PIN to print it.${expiry}`;
    case "follow_me":
      return `Enter your PIN at any Follow-Me printer to print it.${expiry}`;
    default:
      return `This job is being held on the print server.${expiry}`;
  }
}

function stateBadge(job: PrintQueueJob) {
  if (job.state === "printing") return <Badge tone="success">Printing now</Badge>;
  // "Held" on this page always means a person parked the job — an admin on the
  // print server, or PrintOps itself waiting for a release. It is not the
  // printer doing something, and a bare "Held" badge reads as though it were.
  if (job.state === "held") return <Badge tone="neutral">Held by an administrator</Badge>;
  if (job.yielded) return <Badge tone="warning">Letting others go first</Badge>;
  return <Badge tone="neutral">Waiting</Badge>;
}

export default function PrintQueuePage() {
  const currentUser = useCurrentUser();
  // A "View as" session is read-only server-side — every non-GET is 403'd by
  // the middleware in app/main.py, whatever the page thinks. So the buttons
  // are disabled rather than left to fail, matching the Print page.
  const isImpersonating = Boolean(currentUser?.impersonated_by);
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});

  const load = useCallback(async (): Promise<void> => {
    try {
      const view = await getMyPrintQueue();
      setState({ phase: "ok", view });
    } catch (err: unknown) {
      setState({
        phase: "error",
        message: err instanceof Error ? err.message : "Couldn't load the print queue",
      });
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  async function act(job: PrintQueueJob, action: "yield" | "restore") {
    setBusy((prev) => ({ ...prev, [job.cups_job_id]: true }));
    setErrors((prev) => {
      const next = { ...prev };
      delete next[job.cups_job_id];
      return next;
    });
    try {
      if (action === "yield") {
        await yieldPrintQueueJob(job.cups_job_id);
      } else {
        await restorePrintQueueJob(job.cups_job_id);
      }
      await load();
    } catch (err: unknown) {
      setErrors((prev) => ({
        ...prev,
        [job.cups_job_id]:
          err instanceof ApiError ? err.message : "That didn't work — try again.",
      }));
    } finally {
      setBusy((prev) => ({ ...prev, [job.cups_job_id]: false }));
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Print Queue</h1>
        <p className="mt-1 max-w-2xl text-sm text-zinc-600 dark:text-zinc-400">
          What is waiting to print on the printers you have a job on. If you have sent
          something long and someone else needs a page or two, you can move your own
          job to the back of the line — it still prints, once the others have. You can
          put it back at any time before it starts. Anything PrintOps is holding for
          you — over quota, waiting for your PIN, or waiting for a printer to come
          back on — is listed underneath.
        </p>
      </div>

      {state.phase === "loading" && <Spinner />}

      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" &&
        state.view.queues.length === 0 &&
        state.view.held.length === 0 && (
        <EmptyState>
          Nothing of yours is waiting. When you send something to print it appears
          here until the printer picks it up — a job that prints straight away may
          never show up at all, which is the queue working.
        </EmptyState>
      )}

      {state.phase === "ok" &&
        state.view.queues.map((queue) => (
          <Card key={queue.printer_id}>
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <CardTitle>{queue.printer_name}</CardTitle>
              <span className="text-sm text-zinc-500">
                {queue.total_job_count === 1
                  ? "1 job in the queue"
                  : `${queue.total_job_count} jobs in the queue`}
                {queue.my_job_count > 0 && ` · ${queue.my_job_count} yours`}
              </span>
            </div>

            <ol className="space-y-2">
              {queue.jobs.map((job) => (
                <li
                  key={job.cups_job_id}
                  className={`flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2 text-sm ${
                    job.mine
                      ? "border-blue-300 bg-blue-50/60 dark:border-blue-900 dark:bg-blue-950/40"
                      : "border-black/[.08] dark:border-white/[.145]"
                  }`}
                >
                  <span className="w-6 shrink-0 text-center font-mono text-xs text-zinc-500">
                    {job.position}
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    {jobLabel(job)}
                    {job.mine && <span className="ml-2 text-xs text-zinc-500">(yours)</span>}
                  </span>
                  <span className="shrink-0 text-xs text-zinc-500">
                    {formatBytes(job.size_bytes)}
                  </span>
                  <span className="shrink-0">{stateBadge(job)}</span>
                  {(job.can_yield || job.can_restore) && (
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      <Button
                        variant="secondary"
                        className="!px-3 !py-1 text-xs"
                        disabled={busy[job.cups_job_id] || isImpersonating}
                        onClick={() => act(job, job.can_yield ? "yield" : "restore")}
                      >
                        {busy[job.cups_job_id]
                          ? "Working…"
                          : job.can_yield
                            ? "Let others go first"
                            : "Put back in line"}
                      </Button>
                      {errors[job.cups_job_id] && (
                        <span className="max-w-[16rem] text-right text-xs text-red-600 dark:text-red-400">
                          {errors[job.cups_job_id]}
                        </span>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ol>

            {isImpersonating && (
              <p className="mt-3 text-xs text-amber-700 dark:text-amber-400">
                You&rsquo;re viewing this as another user (read-only) — moving jobs is
                disabled during a &quot;View as&quot; session.
              </p>
            )}

            {queue.jobs.some((job) => job.state === "held" && job.mine) && (
              <p className="mt-3 text-xs text-zinc-500">
                A held job is one someone has parked on the print server. It will not
                print until an administrator releases it, and it isn&rsquo;t holding
                anyone else up in the meantime.
              </p>
            )}

            {queue.jobs.some((job) => job.yielded && job.mine) && (
              <p className="mt-3 text-xs text-zinc-500">
                A job that is letting others go first waits behind everything else on
                this printer, including anything sent while it waits. Use{" "}
                <strong>Put back in line</strong> to return it to its original place.
              </p>
            )}
          </Card>
        ))}

      {state.phase === "ok" && state.view.held.length > 0 && (
        <Card>
          <div className="mb-1">
            <CardTitle>Waiting on something else</CardTitle>
          </div>
          <p className="mb-3 text-sm text-zinc-600 dark:text-zinc-400">
            These jobs of yours haven&rsquo;t reached a printer&rsquo;s queue yet. They
            are not holding anyone else up, and nothing has been lost.
          </p>
          <ul className="space-y-2">
            {state.view.held.map((job) => (
              <li
                key={job.job_id}
                className="rounded-lg border border-black/[.08] px-3 py-2 text-sm dark:border-white/[.145]"
              >
                <div className="flex flex-wrap items-center gap-3">
                  <span className="min-w-0 flex-1 truncate">
                    {job.document_name ?? "Your job"}
                    <span className="ml-2 text-xs text-zinc-500">{job.printer_name}</span>
                  </span>
                  <span className="shrink-0 text-xs text-zinc-500">
                    {formatBytes(job.size_bytes)}
                  </span>
                  <span className="shrink-0">{heldBadge(job)}</span>
                </div>
                <p className="mt-1 text-xs text-zinc-500">{heldExplanation(job)}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
