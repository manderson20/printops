"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  cancelScheduledUpdate,
  checkForUpdate,
  listUpdateSchedule,
  scheduleUpdate,
  type UpdateCheck,
  type UpdateScheduleEntry,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

type CheckState =
  | { phase: "loading" }
  | { phase: "ok"; check: UpdateCheck }
  | { phase: "error"; message: string };

const STATUS_TONE: Record<UpdateScheduleEntry["status"], "neutral" | "warning" | "success" | "danger"> = {
  pending: "neutral",
  in_progress: "warning",
  completed: "success",
  failed: "danger",
};

export default function UpdatesPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [checkState, setCheckState] = useState<CheckState>({ phase: "loading" });
  const [schedule, setSchedule] = useState<UpdateScheduleEntry[]>([]);
  const [scheduledAt, setScheduledAt] = useState("");
  const [scheduling, setScheduling] = useState(false);
  const [scheduleError, setScheduleError] = useState<string | null>(null);

  useEffect(() => {
    if (currentUser === null) {
      router.replace("/login");
    } else if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  function fetchCheck() {
    checkForUpdate()
      .then((check) => setCheckState({ phase: "ok", check }))
      .catch((error: unknown) =>
        setCheckState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to check for updates",
        }),
      );
  }

  function loadCheck() {
    setCheckState({ phase: "loading" });
    fetchCheck();
  }

  function loadSchedule() {
    listUpdateSchedule()
      .then(setSchedule)
      .catch(() => {});
  }

  useEffect(() => {
    if (currentUser?.role !== "admin") return;
    // Not loadCheck(): checkState already starts at { phase: "loading" },
    // so the initial fetch doesn't need to re-set it — only the manual
    // "Check for updates" button (a real event handler, not an effect)
    // needs to reset phase back to loading on a later click.
    fetchCheck();
    loadSchedule();
    const interval = setInterval(loadSchedule, 15_000);
    return () => clearInterval(interval);
  }, [currentUser]);

  const activeSchedule = schedule.filter(
    (row) => row.status === "pending" || row.status === "in_progress",
  );
  const lastFailure = schedule.find(
    (row) => row.status === "failed" && row.log && row.log !== "Cancelled by admin.",
  );

  async function handleSchedule() {
    if (checkState.phase !== "ok" || !scheduledAt) return;
    setScheduling(true);
    setScheduleError(null);
    try {
      await scheduleUpdate({
        scheduled_at: new Date(scheduledAt).toISOString(),
        target_version: checkState.check.latest_version,
      });
      setScheduledAt("");
      loadSchedule();
    } catch (err) {
      setScheduleError(err instanceof ApiError ? err.message : "Failed to schedule update");
    } finally {
      setScheduling(false);
    }
  }

  async function handleCancel(id: string) {
    try {
      await cancelScheduledUpdate(id);
      loadSchedule();
    } catch {
      // best-effort — the table will still show it as active until the next poll
    }
  }

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Updates</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Check the version running on this server against the latest on GitHub, and schedule
          when to apply an update.
        </p>
      </div>

      <Card>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold text-black dark:text-zinc-50">Software Update</h2>
          <button
            onClick={loadCheck}
            disabled={checkState.phase === "loading"}
            className="text-xs text-zinc-500 hover:underline disabled:opacity-50"
          >
            {checkState.phase === "loading" ? "Checking…" : "Check for updates"}
          </button>
        </div>

        {checkState.phase === "error" && <ErrorState>{checkState.message}</ErrorState>}

        {checkState.phase === "ok" && (
          <>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              Installed:{" "}
              <strong className="text-black dark:text-zinc-50">
                {checkState.check.current_version}
              </strong>
              {" · "}
              Latest on GitHub:{" "}
              <strong className="text-black dark:text-zinc-50">
                {checkState.check.latest_version}
              </strong>
            </p>

            {!checkState.check.update_available ? (
              <p className="mt-1 text-xs text-emerald-600 dark:text-emerald-400">Up to date.</p>
            ) : (
              <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900/40 dark:bg-amber-950/30">
                <p className="mb-2 text-sm font-medium text-amber-900 dark:text-amber-200">
                  Update available: {checkState.check.current_version} →{" "}
                  {checkState.check.latest_version}
                </p>
                {checkState.check.changelog && (
                  <pre className="mb-3 max-h-48 overflow-y-auto whitespace-pre-wrap rounded border border-amber-100 bg-white p-2 text-xs text-amber-900 dark:border-amber-900/30 dark:bg-black dark:text-amber-200">
                    {checkState.check.changelog}
                  </pre>
                )}

                {activeSchedule.length > 0 ? (
                  <p className="rounded border border-amber-100 bg-white p-2 text-sm text-amber-900 dark:border-amber-900/30 dark:bg-black dark:text-amber-200">
                    Already scheduled for{" "}
                    {new Date(activeSchedule[0].scheduled_at).toLocaleString()} — cancel it below
                    first to pick a different time.
                  </p>
                ) : (
                  <>
                    <div className="flex flex-wrap items-end gap-3">
                      <Field label="Schedule for">
                        <Input
                          type="datetime-local"
                          value={scheduledAt}
                          onChange={(e) => setScheduledAt(e.target.value)}
                        />
                      </Field>
                      <Button onClick={handleSchedule} disabled={!scheduledAt || scheduling}>
                        {scheduling ? "Scheduling…" : "Schedule Update"}
                      </Button>
                    </div>
                    <p className="mt-2 text-xs text-amber-800 dark:text-amber-300">
                      At the scheduled time, this server pulls the new version, runs database
                      migrations, rebuilds, and restarts — printing is briefly interrupted
                      (typically under a minute). A failed update stops without automatically
                      rolling back; check the log below and fix it manually.
                    </p>
                  </>
                )}
                {scheduleError && (
                  <div className="mt-2">
                    <ErrorState>{scheduleError}</ErrorState>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </Card>

      {schedule.length > 0 && (
        <Card className="p-0">
          <h3 className="border-b border-black/[.08] px-4 py-3 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
            Update history
          </h3>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                <th className="px-4 py-3 font-medium">Target</th>
                <th className="px-4 py-3 font-medium">Scheduled</th>
                <th className="px-4 py-3 font-medium">Requested by</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {schedule.map((row) => (
                <tr
                  key={row.id}
                  className="border-b border-black/[.08] last:border-0 dark:border-white/[.145]"
                >
                  <td className="px-4 py-3 text-black dark:text-zinc-50">{row.target_version}</td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {new Date(row.scheduled_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {row.requested_by ?? "—"}
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone={STATUS_TONE[row.status]}>{row.status.replace("_", " ")}</Badge>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {(row.status === "pending" || row.status === "in_progress") && (
                      <button
                        onClick={() => handleCancel(row.id)}
                        className="text-xs text-red-600 hover:underline dark:text-red-400"
                      >
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {lastFailure && (
            <details className="border-t border-black/[.08] p-4 dark:border-white/[.145]">
              <summary className="cursor-pointer text-xs font-semibold text-red-600 dark:text-red-400">
                Most recent failure log
              </summary>
              <pre className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap rounded border border-red-200 bg-red-50 p-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                {lastFailure.log}
              </pre>
            </details>
          )}
        </Card>
      )}
    </div>
  );
}
