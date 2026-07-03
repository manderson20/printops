"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getJobUsage, type UserUsage } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; rows: UserUsage[] }
  | { phase: "error"; message: string };

function rowSearchText(row: UserUsage): string {
  return row.is_other
    ? "other unattributed"
    : `${row.name ?? ""} ${row.email ?? ""}`.toLowerCase();
}

function escapeCsvField(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

function downloadUsageCsv(rows: UserUsage[]) {
  const header = ["Name", "Email", "Jobs", "Pages", "Bytes"];
  const lines = rows.map((row) => [
    row.is_other ? "Other / Unattributed" : row.name ?? "",
    row.is_other ? "" : row.email ?? "",
    String(row.job_count),
    String(row.total_pages),
    String(row.total_bytes),
  ]);
  const csv = [header, ...lines]
    .map((line) => line.map(escapeCsvField).join(","))
    .join("\r\n");

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `printops-usage-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export default function UsagePage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [search, setSearch] = useState("");

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

  const rows = state.phase === "ok" ? state.rows : [];
  const query = search.trim().toLowerCase();
  const filteredRows = query ? rows.filter((row) => rowSearchText(row).includes(query)) : rows;

  return (
    <div className="flex w-full max-w-4xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Usage</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Pages printed and job counts for every synced Google Workspace user, including
          anyone who hasn&rsquo;t printed yet. Volume that couldn&rsquo;t be matched to a
          roster address is rolled into a single &ldquo;Other / Unattributed&rdquo; row.
        </p>
      </div>

      {state.phase === "ok" && rows.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or email…"
            className="w-full max-w-xs"
          />
          <span className="text-xs text-zinc-500">
            {filteredRows.length} of {rows.length}
          </span>
          <Button
            variant="secondary"
            className="ml-auto"
            onClick={() => downloadUsageCsv(filteredRows)}
            disabled={filteredRows.length === 0}
          >
            Export CSV
          </Button>
        </div>
      )}

      {state.phase === "loading" && <Spinner label="Loading usage…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && (
        <Card className="p-0">
          {rows.length === 0 ? (
            <div className="p-6">
              <EmptyState>
                No Google Workspace users synced yet, and no jobs logged.
              </EmptyState>
            </div>
          ) : filteredRows.length === 0 ? (
            <div className="p-6">
              <EmptyState>No users match &ldquo;{search}&rdquo;.</EmptyState>
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
                {filteredRows.map((row) => (
                  <tr
                    key={row.is_other ? "__other__" : row.email}
                    className="border-b border-black/[.08] last:border-0 dark:border-white/[.145]"
                  >
                    <td className="px-4 py-3 text-black dark:text-zinc-50">
                      {row.is_other ? (
                        <>
                          Other / Unattributed
                          <div className="text-xs font-normal text-zinc-500">
                            Not matched to a Workspace address
                          </div>
                        </>
                      ) : (
                        <>
                          {row.name ?? row.email}
                          {row.name && (
                            <div className="text-xs text-zinc-500">{row.email}</div>
                          )}
                        </>
                      )}
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
