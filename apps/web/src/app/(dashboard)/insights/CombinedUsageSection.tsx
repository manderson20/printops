"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  downloadCombinedReportCsv,
  getCombinedReportSummary,
  getCombinedUserLeaderboard,
  type CombinedLeaderboardEntry,
  type CombinedSummary,
  type ReportFilters,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | {
      phase: "ok";
      summary: CombinedSummary;
      leaderboard: CombinedLeaderboardEntry[];
    }
  | { phase: "error"; message: string };

/** Print + walk-up-copy usage together, by staff member — an additive
 * section below the print-only Insights numbers above, not a
 * replacement. Fetches independently of the parent page's own
 * Promise.all bundle (self-contained loading/error state) so a copier
 * accounting issue never blocks the print-only report from rendering. */
export function CombinedUsageSection({ filters }: { filters: ReportFilters }) {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    setState({ phase: "loading" });
    Promise.all([
      getCombinedReportSummary(filters),
      getCombinedUserLeaderboard(filters, 10),
    ])
      .then(([summary, leaderboard]) =>
        setState({ phase: "ok", summary, leaderboard }),
      )
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message:
            error instanceof Error
              ? error.message
              : "Failed to load combined usage",
        }),
      );
  }, [filters]);

  async function handleExport() {
    setExporting(true);
    try {
      await downloadCombinedReportCsv(filters);
    } catch {
      // best-effort — the export button itself doesn't need its own error UI
    } finally {
      setExporting(false);
    }
  }

  if (state.phase === "loading")
    return <Spinner label="Loading combined usage…" />;
  if (state.phase === "error") return <ErrorState>{state.message}</ErrorState>;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-black dark:text-zinc-50">
          Print + Copy Usage
        </h2>
        <Button
          variant="secondary"
          className="print:hidden"
          onClick={handleExport}
          disabled={exporting}
        >
          {exporting ? "Exporting…" : "Export CSV"}
        </Button>
      </div>
      <p className="-mt-4 text-sm text-zinc-500">
        Combines IPP print jobs with walk-up copier activity (Stage 1 copier
        accounting) for the same staff member, using the same date range and
        filters as above.
      </p>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card className="p-4">
          <p className="text-xs font-medium text-zinc-500">Printed pages</p>
          <p className="mt-1 text-2xl font-semibold text-black dark:text-zinc-50">
            {state.summary.print_pages.toLocaleString()}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium text-zinc-500">Copied pages</p>
          <p className="mt-1 text-2xl font-semibold text-black dark:text-zinc-50">
            {state.summary.copy_pages.toLocaleString()}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium text-zinc-500">Total pages</p>
          <p className="mt-1 text-2xl font-semibold text-black dark:text-zinc-50">
            {state.summary.total_pages.toLocaleString()}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium text-zinc-500">
            Unmapped copy activity
          </p>
          <p className="mt-1 text-2xl font-semibold text-black dark:text-zinc-50">
            {state.summary.unmapped_copy_activity_count.toLocaleString()}
          </p>
        </Card>
      </div>

      {state.summary.unmapped_copy_activity_count > 0 && (
        <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {state.summary.unmapped_copy_activity_count} copier usage record
          {state.summary.unmapped_copy_activity_count === 1 ? "" : "s"}{" "}
          couldn&apos;t be matched to a staff member and are excluded from the
          totals above.{" "}
          <Link href="/copier-unmapped" className="font-medium underline">
            Resolve unmapped activity
          </Link>
          .
        </div>
      )}

      <Card>
        <CardTitle className="mb-3">Combined Leaderboard</CardTitle>
        {state.leaderboard.length === 0 ? (
          <EmptyState>No print or copy activity in this range.</EmptyState>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="py-2 font-medium">Staff</th>
                <th className="py-2 font-medium">Printed</th>
                <th className="py-2 font-medium">Copied</th>
                <th className="py-2 font-medium">Total</th>
              </tr>
            </thead>
            <tbody>
              {state.leaderboard.map((entry) => (
                <tr
                  key={entry.key}
                  className="border-t border-black/[.08] dark:border-white/[.1]"
                >
                  <td className="py-2 text-black dark:text-zinc-50">
                    {entry.label}
                  </td>
                  <td className="py-2 text-zinc-600 dark:text-zinc-400">
                    {entry.print_pages.toLocaleString()}
                  </td>
                  <td className="py-2 text-zinc-600 dark:text-zinc-400">
                    {entry.copy_pages > 0 ? (
                      entry.copy_pages.toLocaleString()
                    ) : (
                      <Badge tone="neutral">0</Badge>
                    )}
                  </td>
                  <td className="py-2 font-medium text-black dark:text-zinc-50">
                    {entry.total_pages.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
