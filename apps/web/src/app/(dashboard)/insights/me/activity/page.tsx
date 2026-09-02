"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  getMyActivity,
  type ExplainedPeriod,
  type MyActivity,
  type MyActivityRow,
} from "@/lib/api";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { PeriodPicker, formatNumber } from "../../explained-ui";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; data: MyActivity }
  | { phase: "error"; message: string };

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

/** A print gets a time. A copy gets the window between the two counter
 * readings it was derived from — the machines cannot report when within
 * that window it happened, so the page never implies they can. */
function when(row: MyActivityRow): string {
  if (row.at) return clockTime(row.at);
  if (row.window_start && row.window_end) {
    const start = clockTime(row.window_start);
    const end = clockTime(row.window_end);
    return start === end ? start : `between ${start} and ${end}`;
  }
  return "—";
}

function rowDate(row: MyActivityRow): string {
  const iso = row.at ?? row.window_end ?? row.window_start;
  return iso ? shortDate(iso) : "—";
}

function details(row: MyActivityRow): string {
  const parts: string[] = [];
  if (row.kind === "print") {
    if (row.color_mode === "color") parts.push("colour");
    else if (row.color_mode === "monochrome") parts.push("mono");
    if (row.duplex === true) parts.push("double-sided");
    else if (row.duplex === false) parts.push("single-sided");
  } else {
    if (row.color_pages) parts.push(`${formatNumber(row.color_pages)} colour`);
    if (row.mono_pages) parts.push(`${formatNumber(row.mono_pages)} mono`);
  }
  return parts.join(", ");
}

const KIND_STYLES: Record<string, string> = {
  print: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  copy: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
};

function KindBadge({ row }: { row: MyActivityRow }) {
  const label = row.kind === "print" ? "Print" : row.activity_type;
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
        KIND_STYLES[row.kind] ?? KIND_STYLES.copy
      }`}
    >
      {label}
    </span>
  );
}

export default function MyActivityPage() {
  const [period, setPeriod] = useState<ExplainedPeriod>("month");
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [loadedPeriod, setLoadedPeriod] = useState(period);

  if (period !== loadedPeriod) {
    setLoadedPeriod(period);
    setState({ phase: "loading" });
  }

  useEffect(() => {
    let cancelled = false;
    getMyActivity(period)
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
          <h1 className="text-2xl font-semibold tracking-tight">My activity</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Every job you printed and every copy you made, line by line.{" "}
            <Link
              href="/insights/me"
              className="underline underline-offset-2 hover:text-zinc-700 dark:hover:text-zinc-300"
            >
              Back to your summary
            </Link>
            .
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
        <ErrorState>Couldn&apos;t load your activity: {state.message}</ErrorState>
      ) : null}

      {state.phase === "ok" && state.data.rows.length === 0 ? (
        <EmptyState>
          Nothing recorded for this period yet.
        </EmptyState>
      ) : null}

      {state.phase === "ok" && state.data.rows.length > 0 ? (
        <>
          <div className="overflow-x-auto rounded-xl border border-black/[.08] dark:border-white/[.145]">
            <table className="w-full min-w-[44rem] text-sm">
              <thead>
                <tr className="border-b border-black/[.08] text-left text-[11px] uppercase tracking-wider text-zinc-500 dark:border-white/[.145]">
                  <th className="px-4 py-3 font-semibold">Date</th>
                  <th className="px-4 py-3 font-semibold">Type</th>
                  <th className="px-4 py-3 font-semibold">What</th>
                  <th className="px-4 py-3 font-semibold">Where</th>
                  <th className="px-4 py-3 text-right font-semibold">Pages</th>
                  <th className="px-4 py-3 font-semibold">When</th>
                </tr>
              </thead>
              <tbody>
                {state.data.rows.map((row, index) => (
                  <tr
                    key={`${row.kind}-${index}-${row.at ?? row.window_end}`}
                    className="border-b border-black/[.05] last:border-0 dark:border-white/[.08]"
                  >
                    <td className="whitespace-nowrap px-4 py-3 text-zinc-500">
                      {rowDate(row)}
                    </td>
                    <td className="px-4 py-3">
                      <KindBadge row={row} />
                    </td>
                    <td className="px-4 py-3">
                      <span className="block">{row.label}</span>
                      {details(row) ? (
                        <span className="block text-xs text-zinc-500">
                          {details(row)}
                        </span>
                      ) : null}
                    </td>
                    <td className="px-4 py-3 text-zinc-500">{row.where}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {formatNumber(row.pages)}
                    </td>
                    <td
                      className={`whitespace-nowrap px-4 py-3 ${
                        row.at ? "" : "text-zinc-500"
                      }`}
                    >
                      {when(row)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-xs leading-relaxed text-zinc-500">
            Showing {formatNumber(state.data.rows.length)} of{" "}
            {formatNumber(state.data.total_rows)} entries, {state.data.range_start}{" "}
            to {state.data.range_end}. Printing is listed per job.
            {state.data.includes_period_derived_copies
              ? " Copying is listed per meter reading: a copier reports a running total rather than individual copies, so each copy row covers the time between two readings and shows a range instead of a time. The machines cannot report the exact moment a copy was made."
              : null}
          </p>
        </>
      ) : null}
    </div>
  );
}
