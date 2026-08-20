"use client";

import { useEffect, useState } from "react";
import {
  getStaffUsage,
  type ReportFilters,
  type StaffUsage,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

function money(value: number) {
  return `$${value.toFixed(2)}`;
}

function pages(value: number) {
  return value.toLocaleString();
}

/** One person's print and copy usage, broken down by the machine that did
 * it. Shared by the expandable Combined Leaderboard row and the standalone
 * /insights/staff/[email] page so the two can never drift into telling
 * slightly different stories about the same person. */
export function StaffUsageDetail({
  email,
  filters,
}: {
  email: string;
  filters: ReportFilters;
}) {
  const [state, setState] = useState<
    | { phase: "loading" }
    | { phase: "ok"; usage: StaffUsage }
    | { phase: "error"; message: string }
  >({ phase: "loading" });
  const [prevInputs, setPrevInputs] = useState({ email, filters });

  // Reset to "loading" the instant the inputs change, computed during
  // render rather than via an effect + setState — same pattern (and same
  // reason) as CombinedUsageSection: it avoids a render of the previous
  // person's numbers under the new person's name.
  if (prevInputs.email !== email || prevInputs.filters !== filters) {
    setPrevInputs({ email, filters });
    setState({ phase: "loading" });
  }

  useEffect(() => {
    let cancelled = false;
    getStaffUsage(email, filters)
      .then((usage) => {
        if (!cancelled) setState({ phase: "ok", usage });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          phase: "error",
          message:
            error instanceof Error ? error.message : "Failed to load usage",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [email, filters]);

  if (state.phase === "loading") return <Spinner label="Loading usage…" />;
  if (state.phase === "error") return <ErrorState>{state.message}</ErrorState>;

  const { usage } = state;
  // Copies the device counted but never broke out by colour. Shown as its
  // own number rather than folded into mono, which is what it is *priced*
  // as but not what the device said.
  const unsplitCopyPages = usage.copiers.reduce(
    (sum, c) => sum + Math.max(c.pages - c.color_pages - c.mono_pages, 0),
    0,
  );

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Printed" value={pages(usage.print_pages)} sub={money(usage.print_cost)} />
        <Stat label="Copied" value={pages(usage.copy_pages)} sub={money(usage.copy_cost)} />
        <Stat label="Total pages" value={pages(usage.total_pages)} sub={`${pages(usage.sheets)} sheets`} />
        <Stat label="Total cost" value={money(usage.total_cost)} sub="toner + paper" emphasis />
      </div>

      <section>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Print jobs
        </h4>
        {usage.printers.length === 0 ? (
          <EmptyState>No print jobs in this range.</EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-zinc-500">
                <tr>
                  <th className="py-2 font-medium">Printer</th>
                  <th className="py-2 font-medium">Jobs</th>
                  <th className="py-2 font-medium">Pages</th>
                  <th className="py-2 font-medium">Color / Mono</th>
                  <th className="py-2 font-medium">Duplex / Simplex</th>
                  <th className="py-2 font-medium">Sheets</th>
                  <th className="py-2 font-medium">Toner</th>
                  <th className="py-2 font-medium">Paper</th>
                  <th className="py-2 font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {usage.printers.map((p) => (
                  <tr
                    key={p.printer_id}
                    className="border-t border-black/[.08] dark:border-white/[.1]"
                  >
                    <td className="py-2 text-black dark:text-zinc-50">
                      {p.printer_name}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {pages(p.job_count)}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {pages(p.pages)}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {pages(p.color_pages)} / {pages(p.mono_pages)}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {pages(p.duplex_pages)} / {pages(p.simplex_pages)}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {pages(p.sheets)}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {money(p.toner_cost)}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {money(p.paper_cost)}
                    </td>
                    <td className="py-2 font-medium text-black dark:text-zinc-50">
                      {money(p.total_cost)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Copier activity
        </h4>
        {usage.copiers.length === 0 ? (
          <EmptyState>No walk-up copier activity in this range.</EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-zinc-500">
                <tr>
                  <th className="py-2 font-medium">Copier</th>
                  <th className="py-2 font-medium">Copies</th>
                  <th className="py-2 font-medium">Color / Mono</th>
                  <th className="py-2 font-medium">Scanned</th>
                  <th className="py-2 font-medium">Faxed</th>
                  <th className="py-2 font-medium">Sheets</th>
                  <th className="py-2 font-medium">Toner</th>
                  <th className="py-2 font-medium">Paper</th>
                  <th className="py-2 font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {usage.copiers.map((c) => (
                  <tr
                    key={c.device_id}
                    className="border-t border-black/[.08] dark:border-white/[.1]"
                  >
                    <td className="py-2 text-black dark:text-zinc-50">
                      <span className="flex flex-wrap items-center gap-1.5">
                        {c.device_name}
                        {c.attributed_by_default_owner && (
                          <Badge tone="neutral">Whole-device</Badge>
                        )}
                      </span>
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {pages(c.pages)}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {c.measured_color ? (
                        <>
                          {pages(c.color_pages)} / {pages(c.mono_pages)}
                        </>
                      ) : (
                        <span title="This copier reports a copy total with no color breakdown. The pages are priced at the mono rate.">
                          Not reported
                        </span>
                      )}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {pages(c.scan_pages)}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {pages(c.fax_pages)}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {pages(c.sheets)}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {money(c.toner_cost)}
                    </td>
                    <td className="py-2 text-zinc-600 dark:text-zinc-400">
                      {money(c.paper_cost)}
                    </td>
                    <td className="py-2 font-medium text-black dark:text-zinc-50">
                      {money(c.total_cost)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Said plainly rather than left for someone to work out from a
          number that looks lower than they expected. */}
      {(unsplitCopyPages > 0 || usage.copiers.some((c) => c.attributed_by_default_owner)) && (
        <div className="rounded border border-black/[.08] p-3 text-xs text-zinc-500 dark:border-white/[.1]">
          <p className="font-medium text-zinc-600 dark:text-zinc-400">
            How these copy numbers were arrived at
          </p>
          <ul className="mt-1 list-disc space-y-1 pl-4">
            {unsplitCopyPages > 0 && (
              <li>
                {pages(unsplitCopyPages)} copied page
                {unsplitCopyPages === 1 ? "" : "s"} came from a meter that
                doesn&apos;t separate color from mono, and are priced at the
                mono rate — so the real cost may be higher.
              </li>
            )}
            {usage.copiers.some((c) => c.attributed_by_default_owner) && (
              <li>
                Pages marked <em>Whole-device</em> are every copy made on a
                copier nobody logs in to, credited here because an admin named
                this person as its owner.
              </li>
            )}
            <li>
              Copiers don&apos;t report duplex per copy, so copy paper is
              counted one sheet per page — an over-estimate wherever people
              duplex.
            </li>
          </ul>
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  emphasis,
}: {
  label: string;
  value: string;
  sub?: string;
  emphasis?: boolean;
}) {
  return (
    <div
      className={`rounded border p-3 ${
        emphasis
          ? "border-black/[.14] dark:border-white/[.18]"
          : "border-black/[.08] dark:border-white/[.1]"
      }`}
    >
      <p className="text-xs font-medium text-zinc-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-black dark:text-zinc-50">
        {value}
      </p>
      {sub && <p className="mt-0.5 text-xs text-zinc-500">{sub}</p>}
    </div>
  );
}
