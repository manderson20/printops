"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getJobUsage, type UserUsage } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; rows: UserUsage[] }
  | { phase: "error"; message: string };

export default function UsagePage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [state, setState] = useState<LoadState>({ phase: "loading" });

  useEffect(() => {
    if (currentUser === null) {
      router.replace("/login");
    } else if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  useEffect(() => {
    if (currentUser?.role !== "admin") return;
    getJobUsage()
      .then((rows) => setState({ phase: "ok", rows }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load usage",
        }),
      );
  }, [currentUser]);

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="flex w-full max-w-4xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Usage</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Pages printed and job counts per user, across all printers.
        </p>
      </div>

      {state.phase === "loading" && <Spinner label="Loading usage…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && (
        <Card className="p-0">
          {state.rows.length === 0 ? (
            <div className="p-6">
              <EmptyState>No jobs logged yet.</EmptyState>
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                  <th className="px-4 py-3 font-medium">User</th>
                  <th className="px-4 py-3 font-medium">Jobs</th>
                  <th className="px-4 py-3 font-medium">Pages</th>
                  <th className="px-4 py-3 font-medium">Size</th>
                </tr>
              </thead>
              <tbody>
                {state.rows.map((row) => (
                  <tr
                    key={row.submitted_by ?? "unknown"}
                    className="border-b border-black/[.08] last:border-0 dark:border-white/[.145]"
                  >
                    <td className="px-4 py-3 text-black dark:text-zinc-50">
                      {row.submitted_by ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">{row.job_count}</td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {row.total_pages}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {formatBytes(row.total_bytes)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </div>
  );
}
