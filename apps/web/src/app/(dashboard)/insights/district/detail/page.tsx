"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  getDistrictDetail,
  type DistrictDetail,
  type DistrictSegment,
  type ExplainedPeriod,
} from "@/lib/api";
import { formatCurrency } from "@/lib/format";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { PeriodPicker, StatCard, formatNumber } from "../../explained-ui";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; data: DistrictDetail }
  | { phase: "error"; message: string };

export default function DistrictDetailPage() {
  const [period, setPeriod] = useState<ExplainedPeriod>("year");
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [loadedPeriod, setLoadedPeriod] = useState(period);

  // Reset to "loading" the instant the period changes, computed during
  // render rather than via an effect + setState — the same idiom
  // @/lib/useCurrentUser uses, and for the same reason: it keeps the
  // previous period's numbers from showing through for a render or two
  // under the new period's heading.
  if (period !== loadedPeriod) {
    setLoadedPeriod(period);
    setState({ phase: "loading" });
  }
  const currentUser = useCurrentUser();
  const router = useRouter();

  // The API is the real gate (require_role("admin") on the endpoint) —
  // this only avoids showing a non-admin a page that would 403 anyway.
  // Waits past `undefined` so a real admin isn't bounced mid-fetch.
  useEffect(() => {
    if (currentUser === undefined || currentUser === null) return;
    if (currentUser.role !== "admin") router.replace("/insights/district");
  }, [currentUser, router]);

  useEffect(() => {
    let cancelled = false;
    getDistrictDetail(period)
      .then((data) => {
        if (!cancelled) setState({ phase: "ok", data });
      })
      .catch((error: Error) => {
        if (!cancelled) setState({ phase: "error", message: error.message });
      });
    return () => {
      cancelled = true;
    };
  }, [period]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            District detail
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            The same totals as the{" "}
            <Link
              href="/insights/district"
              className="underline underline-offset-2 hover:text-zinc-700 dark:hover:text-zinc-300"
            >
              all-staff view
            </Link>
            , broken down by building and department. Admin only.
          </p>
        </div>
        <PeriodPicker
          value={period}
          onChange={setPeriod}
          disabled={state.phase === "loading"}
        />
      </div>

      {state.phase === "loading" ? <Spinner /> : null}
      {state.phase === "error" ? (
        <ErrorState>Couldn&apos;t load the breakdown: {state.message}</ErrorState>
      ) : null}

      {state.phase === "ok" ? <Detail data={state.data} /> : null}
    </div>
  );
}

function Detail({ data }: { data: DistrictDetail }) {
  return (
    <div className="flex flex-col gap-8">
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          accent
          label="Pages altogether"
          value={formatNumber(data.total_pages)}
          caption={`${formatNumber(data.print_pages)} printed, ${formatNumber(
            data.copy_pages,
          )} copied.`}
        />
        <StatCard
          label="People"
          value={formatNumber(data.contributors)}
          caption="Printed or copied this period."
        />
        <StatCard
          label="Median per person"
          value={formatNumber(data.district_median_pages)}
          caption={`The mean is ${formatNumber(
            data.district_mean_pages,
          )} — a few heavy users pull it up.`}
        />
        <StatCard
          label="Sheets of paper"
          value={formatNumber(data.sheets)}
          caption="Across the whole district."
        />
      </section>

      <SegmentTable
        title="By building"
        noun="building"
        segments={data.by_building}
        total={data.total_pages}
      />
      <SegmentTable
        title="By department"
        noun="department"
        segments={data.by_department}
        total={data.total_pages}
      />
    </div>
  );
}

// The warning belongs to the table it describes, not to the page. Both
// breakdowns now carry an Unassigned row, and they are unassigned for
// different reasons — a device with no building set, versus a copier with
// no linked printer to take a department from — so one shared sentence
// above both tables would have been wrong about one of them.
function SegmentTable({
  title,
  noun,
  segments,
  total,
}: {
  title: string;
  noun: "building" | "department";
  segments: DistrictSegment[];
  total: number;
}) {
  const unassigned = segments.find((s) => s.key === "__unassigned__");
  const unassignedShare =
    unassigned && total > 0
      ? Math.round((unassigned.total_pages / total) * 100)
      : 0;

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h2>
      {unassigned && unassignedShare >= 10 ? (
        <p className="rounded-lg border border-amber-300/60 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-950/40 dark:text-amber-200">
          <strong>{unassignedShare}% of pages sit in “Unassigned.”</strong>{" "}
          {noun === "building"
            ? "Those came from printers and copiers with no building recorded, so the rows below only cover what is left. Setting a building on those devices is what moves them into the right row."
            : "Those came from printers with no department recorded, and from copiers not linked to a printer to take one from, so the rows below only cover what is left."}
        </p>
      ) : null}
      {segments.length === 0 ? (
        <EmptyState>Nothing recorded for this period.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-black/[.08] dark:border-white/[.145]">
          <table className="w-full min-w-[46rem] text-sm">
            <thead>
              <tr className="border-b border-black/[.08] text-left text-[11px] uppercase tracking-wider text-zinc-500 dark:border-white/[.145]">
                <th className="px-4 py-3 font-semibold">Name</th>
                <th className="px-4 py-3 text-right font-semibold">People</th>
                <th className="px-4 py-3 text-right font-semibold">Print</th>
                <th className="px-4 py-3 text-right font-semibold">Copy</th>
                <th className="px-4 py-3 text-right font-semibold">Total</th>
                <th className="px-4 py-3 text-right font-semibold">Share</th>
                <th className="px-4 py-3 text-right font-semibold">Cost</th>
              </tr>
            </thead>
            <tbody>
              {segments.map((segment) => (
                <tr
                  key={segment.key}
                  className="border-b border-black/[.05] last:border-0 dark:border-white/[.08]"
                >
                  <td className="px-4 py-3">
                    {segment.key === "__unassigned__" ? (
                      <span className="text-zinc-500">{segment.label}</span>
                    ) : (
                      segment.label
                    )}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {segment.people === 0 ? (
                      <span className="text-zinc-400">—</span>
                    ) : (
                      formatNumber(segment.people)
                    )}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatNumber(segment.print_pages)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatNumber(segment.copy_pages)}
                  </td>
                  <td className="px-4 py-3 text-right font-medium tabular-nums">
                    {formatNumber(segment.total_pages)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-zinc-500">
                    {total > 0
                      ? `${Math.round((segment.total_pages / total) * 100)}%`
                      : "—"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {formatCurrency(segment.estimated_cost)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
