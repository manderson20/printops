"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  getPersonalExplained,
  type ExplainedPeriod,
  type PersonalExplained,
} from "@/lib/api";
import { formatCurrency } from "@/lib/format";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import {
  EquivalencyCards,
  PeriodPicker,
  StatCard,
  formatNumber,
} from "../explained-ui";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; data: PersonalExplained }
  | { phase: "error"; message: string };

function percent(part: number, whole: number): string {
  if (whole <= 0) return "—";
  return `${Math.round((part / whole) * 100)}%`;
}

export default function MyPrintingPage() {
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

  useEffect(() => {
    let cancelled = false;
    getPersonalExplained(period)
      .then((data) => {
        if (!cancelled) setState({ phase: "ok", data });
      })
      .catch((error: Error) => {
        if (!cancelled)
          setState({ phase: "error", message: error.message });
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
            Your printing, explained
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Your own numbers, and what they look like in the real world.{" "}
            <Link
              href="/insights/district"
              className="underline underline-offset-2 hover:text-zinc-700 dark:hover:text-zinc-300"
            >
              See how the whole district is doing
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
        <ErrorState>Couldn&apos;t load your numbers: {state.message}</ErrorState>
      ) : null}

      {state.phase === "ok" ? <Explained data={state.data} /> : null}
    </div>
  );
}

function Explained({ data }: { data: PersonalExplained }) {
  if (data.total_pages === 0) {
    return (
      <EmptyState>
        Nothing to show for this period yet — once you print or copy
        something, your numbers will appear here.
      </EmptyState>
    );
  }

  const colorPages = data.color_pages;
  const knownColorPages = data.color_pages + data.mono_pages;
  const duplexPages = data.duplex_pages;
  const knownDuplexPages = data.duplex_pages + data.simplex_pages;

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            accent
            label="Pages"
            value={formatNumber(data.total_pages)}
            caption={
              data.copy_pages > 0
                ? `${formatNumber(data.print_pages)} printed and ${formatNumber(
                    data.copy_pages,
                  )} copied at the glass.`
                : `Across ${formatNumber(data.job_count)} print jobs — about ${
                    data.avg_pages_per_job
                  } a job.`
            }
          />
          <StatCard
            label="Sheets of paper"
            value={formatNumber(data.sheets)}
            caption="What actually came out of the tray."
          />
          <StatCard
            label="Colour"
            value={percent(colorPages, knownColorPages)}
            caption={
              knownColorPages > 0
                ? `${formatNumber(colorPages)} of ${formatNumber(
                    knownColorPages,
                  )} pages where the printer reported it.`
                : "No printer reported a colour mode this period."
            }
          />
          <StatCard
            label="Double-sided"
            value={percent(duplexPages, knownDuplexPages)}
            caption={
              knownDuplexPages > 0
                ? `${formatNumber(duplexPages)} of ${formatNumber(
                    knownDuplexPages,
                  )} pages.`
                : "No printer reported a duplex setting this period."
            }
          />
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Largest job"
            value={
              data.largest_job_pages === null
                ? "—"
                : formatNumber(data.largest_job_pages)
            }
            caption="Your biggest single document."
          />
          <StatCard
            label="Compared to the district"
            value={
              data.times_district_median === null
                ? "—"
                : `${data.times_district_median}×`
            }
            caption={`The median is ${formatNumber(
              data.district_median_pages,
            )} pages. Roles differ — this isn't a ranking.`}
          />
          <StatCard
            label="Estimated cost"
            value={formatCurrency(data.total_cost)}
            caption="At district toner and paper rates."
          />
          <StatCard
            label="Still on the table"
            value={formatNumber(data.additional_sheets_if_all_duplex)}
            caption={
              data.duplex_sheets_saved > 0
                ? `Sheets double-siding would still save. It has already saved ${formatNumber(
                    data.duplex_sheets_saved,
                  )}.`
                : "Sheets you'd save by printing double-sided."
            }
          />
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
          What that looks like
        </h2>
        <EquivalencyCards
          equivalencies={data.equivalencies}
          skip={["sheets_per_student"]}
        />
      </section>

      {data.facts.length > 0 ? (
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
            In other words
          </h2>
          <ul className="flex flex-col gap-2">
            {data.facts.map((fact) => (
              <li
                key={fact}
                className="rounded-lg border border-black/[.08] bg-white px-4 py-3 text-sm dark:border-white/[.145] dark:bg-black"
              >
                {fact}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <p className="text-xs leading-relaxed text-zinc-500">
        {data.range_start} to {data.range_end}.
        {data.includes_period_derived_copies
          ? " Walk-up copier pages come from each machine's own account counters, which report a total for a period rather than individual copies — so copies have no time of day, and busiest-hour figures are left out rather than shown for printing alone."
          : null}
      </p>
    </div>
  );
}
