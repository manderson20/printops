"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getJobUsage, type UserUsage } from "@/lib/api";
import { formatCurrency } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

const PAGE_SIZE = 50;
// Large enough to cover a full district roster in one request for CSV
// export — kept separate from the table's own PAGE_SIZE so the visible
// table can stay paginated while "Export CSV" still exports everything
// matching the current search, not just the current page.
const EXPORT_PAGE_SIZE = 5000;

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; rows: UserUsage[]; total: number }
  | { phase: "error"; message: string };

function escapeCsvField(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

function downloadUsageCsv(rows: UserUsage[]) {
  const header = [
    "Name",
    "Email",
    "Jobs",
    "Pages",
    "Duplex Pages",
    "Simplex Pages",
    "Mono Pages",
    "Color Pages",
    "Estimated Cost",
  ];
  const lines = rows.map((row) => [
    row.is_other ? "Other / Unattributed" : row.name ?? "",
    row.is_other ? "" : row.email ?? "",
    String(row.job_count),
    String(row.total_pages),
    String(row.duplex_pages),
    String(row.simplex_pages),
    String(row.mono_pages),
    String(row.color_pages),
    row.estimated_cost.toFixed(2),
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
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    if (currentUser === null) {
      router.replace("/login");
    } else if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  useEffect(() => {
    if (currentUser?.role !== "admin") return;
    getJobUsage({ page, pageSize: PAGE_SIZE, search: search || undefined })
      .then((result) => setState({ phase: "ok", rows: result.items, total: result.total }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load usage",
        }),
      );
  }, [currentUser, page, search]);

  function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  }

  async function handleExport() {
    setExporting(true);
    setExportError(null);
    try {
      const result = await getJobUsage({
        page: 1,
        pageSize: EXPORT_PAGE_SIZE,
        search: search || undefined,
      });
      downloadUsageCsv(result.items);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Failed to export usage");
    } finally {
      setExporting(false);
    }
  }

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  const rows = state.phase === "ok" ? state.rows : [];
  const total = state.phase === "ok" ? state.total : 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Usage</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Pages printed, job counts, and estimated cost for every synced Google Workspace user,
          including anyone who hasn&rsquo;t printed yet. Volume that couldn&rsquo;t be matched to
          a roster address is rolled into a single &ldquo;Other / Unattributed&rdquo; row.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <form onSubmit={handleSearchSubmit} className="flex items-center gap-2">
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search name/email, or *domain.org for a domain…"
            className="w-full max-w-xs"
          />
          <Button type="submit" variant="secondary">
            Search
          </Button>
          {search && (
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setSearchInput("");
                setSearch("");
                setPage(1);
              }}
            >
              Clear
            </Button>
          )}
        </form>
        <span className="text-xs text-zinc-500">{total.toLocaleString()} users</span>
        <Button
          variant="secondary"
          className="ml-auto"
          onClick={handleExport}
          disabled={exporting || total === 0}
        >
          {exporting ? "Exporting…" : "Export CSV"}
        </Button>
      </div>
      {exportError && <ErrorState>{exportError}</ErrorState>}

      {state.phase === "loading" && <Spinner label="Loading usage…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && (
        <Card className="overflow-hidden p-0">
          {rows.length === 0 ? (
            <div className="p-6">
              <EmptyState>
                {search
                  ? `No users match "${search}".`
                  : "No Google Workspace users synced yet, and no jobs logged."}
              </EmptyState>
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                  <th className="px-4 py-3 font-medium">User</th>
                  <th className="px-4 py-3 font-medium">Jobs</th>
                  <th className="px-4 py-3 font-medium">Pages</th>
                  <th className="px-4 py-3 font-medium">Duplex</th>
                  <th className="px-4 py-3 font-medium">Simplex</th>
                  <th className="px-4 py-3 font-medium">Mono</th>
                  <th className="px-4 py-3 font-medium">Color</th>
                  <th className="px-4 py-3 font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.is_other ? "__other__" : row.email}
                    onClick={
                      row.is_other
                        ? undefined
                        : () => router.push(`/usage/${encodeURIComponent(row.email!)}`)
                    }
                    className={`border-b border-black/[.08] last:border-0 dark:border-white/[.145] ${
                      row.is_other ? "" : "cursor-pointer hover:bg-black/[.02] dark:hover:bg-white/[.03]"
                    }`}
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
                      {row.duplex_pages}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {row.simplex_pages}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {row.mono_pages}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {row.color_pages}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {formatCurrency(row.estimated_cost)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {state.phase === "ok" && rows.length > 0 && (
        <div className="flex items-center justify-between text-xs text-zinc-500">
          <span>
            Page {page} of {totalPages}
          </span>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              className="!px-2 !py-1 text-xs"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              className="!px-2 !py-1 text-xs"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
