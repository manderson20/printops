"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  getDistrictFunFacts,
  type DistrictFunFacts,
  type ExplainedPeriod,
} from "@/lib/api";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { useCurrentUser } from "@/lib/useCurrentUser";
import {
  EquivalencyCards,
  MilestoneBar,
  PeriodPicker,
  StatCard,
  formatNumber,
} from "../explained-ui";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; data: DistrictFunFacts }
  | { phase: "error"; message: string };

/** Which fact leads the page. Seeded by the date rather than randomised
 * per render, so the page feels fresh between visits without reshuffling
 * under someone standing in the lobby watching it. */
function factOfTheDay(facts: string[]): string | null {
  if (facts.length === 0) return null;
  const today = new Date();
  const dayNumber = Math.floor(
    Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()) / 86_400_000,
  );
  return facts[dayNumber % facts.length];
}

export default function DistrictFunFactsPage() {
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

  useEffect(() => {
    let cancelled = false;
    getDistrictFunFacts(period)
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

  const data = state.phase === "ok" ? state.data : null;
  const hero = useMemo(() => factOfTheDay(data?.facts ?? []), [data?.facts]);
  const distance = data?.equivalencies.find((e) => e.key === "distance") ?? null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Together, we print
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Everything below is the whole district added up — no names, no
            buildings, no departments.{" "}
            <Link
              href="/insights/me"
              className="underline underline-offset-2 hover:text-zinc-700 dark:hover:text-zinc-300"
            >
              See your own numbers
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
        <ErrorState>Couldn&apos;t load the district figures: {state.message}</ErrorState>
      ) : null}

      {data && !data.has_enough_activity ? (
        <EmptyState>
          Not enough activity in this period yet to show district figures.
          Totals appear once enough people have printed that no single
          person&apos;s share can be worked out from them.
        </EmptyState>
      ) : null}

      {data && data.has_enough_activity ? (
        <div className="flex flex-col gap-8">
          <section className="rounded-2xl bg-zinc-900 p-7 sm:p-10 dark:bg-zinc-950">
            <span className="block text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
              Together this {period === "year" ? "school year" : period}
            </span>
            <p className="mt-3 text-balance text-2xl font-semibold leading-tight tracking-tight text-white sm:text-4xl">
              {hero}
            </p>
            <MilestoneBar milestone={distance?.milestone ?? null} collective />
          </section>

          <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              accent
              large
              label="Pages altogether"
              value={formatNumber(data.total_pages)}
              caption={`${formatNumber(data.print_pages)} printed, ${formatNumber(
                data.copy_pages,
              )} copied at the glass.`}
            />
            <StatCard
              label="Sheets of paper"
              value={formatNumber(data.sheets)}
              caption="What actually came out of the trays."
            />
            <StatCard
              label="People"
              value={formatNumber(data.contributors)}
              caption="Everyone who printed or copied this period."
            />
            <StatCard
              label="Per person"
              value={formatNumber(
                data.contributors > 0
                  ? data.total_pages / data.contributors
                  : 0,
              )}
              caption="Pages on average, across everybody."
            />
          </section>

          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
              What that looks like
            </h2>
            <EquivalencyCards equivalencies={data.equivalencies} />
          </section>

          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
              Every way of putting it
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

          <p className="text-xs leading-relaxed text-zinc-500">
            {data.range_start} to {data.range_end}. Printed pages come from the
            print server; copied pages come from each copier&apos;s own account
            counters, which report a total for a period rather than individual
            copies.
            {currentUser?.role === "admin" ? (
              <>
                {" "}
                <Link
                  href="/insights/district/detail"
                  className="underline underline-offset-2"
                >
                  Break this down by building
                </Link>
                .
              </>
            ) : null}
          </p>
        </div>
      ) : null}
    </div>
  );
}
