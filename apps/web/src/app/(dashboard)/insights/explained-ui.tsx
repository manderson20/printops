"use client";

import { useState, type ReactNode } from "react";
import type { Equivalency, ExplainedPeriod, MilestoneProgress } from "@/lib/api";
import { TreesPanel } from "./TreesPanel";

// Display-only conversions. The server sends each equivalency in one base
// unit (feet, pounds, litres) and lets the client decide how to say it,
// because the same number reads naturally as inches on a personal page
// and miles on the district one — see MilestoneOut in
// app/schemas/report.py.
const FEET_PER_MILE = 5280;
const INCHES_PER_FOOT = 12;

export const PERIODS: { value: ExplainedPeriod; label: string }[] = [
  { value: "week", label: "This week" },
  { value: "month", label: "This month" },
  { value: "semester", label: "This semester" },
  { value: "year", label: "School year" },
];

export function formatNumber(value: number, digits = 0): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** One decimal below ten, none above — "0 miles" is a rounding error
 * presented as a fact, and "1,234.6 sheets" is false precision. */
function scaled(value: number): string {
  if (value >= 100) return formatNumber(value);
  if (value >= 10) return formatNumber(value, value % 1 < 0.05 ? 0 : 1);
  return formatNumber(value, 1);
}

export type EquivalencyDisplay = {
  label: string;
  value: string;
  caption: string | null;
};

/** Turns one equivalency into a card's worth of text. Returns null for a
 * key this build doesn't render, so a fact added server-side never
 * crashes an older page. */
export function describeEquivalency(
  equivalency: Equivalency,
): EquivalencyDisplay | null {
  const { key, value, milestone } = equivalency;
  const passed = milestone?.passed?.label ?? null;

  switch (key) {
    case "distance":
      return {
        label: "End to end",
        value:
          value >= FEET_PER_MILE
            ? `${scaled(value / FEET_PER_MILE)} mi`
            : `${scaled(value)} ft`,
        caption: passed ? `That's ${passed}.` : null,
      };
    case "stack_height":
      return {
        label: "Stacked up",
        value:
          value < 1
            ? `${scaled(value * INCHES_PER_FOOT)} in`
            : `${scaled(value)} ft`,
        caption: passed ? `Taller than ${passed}.` : null,
      };
    case "weight":
      return {
        label: "What it weighs",
        value: value >= 2000 ? `${scaled(value / 2000)} tons` : `${scaled(value)} lb`,
        caption: passed ? `Heavier than ${passed}.` : null,
      };
    case "trees":
      // The divisor is not written here any more. It was "8,300" while
      // the cost report used 8,333 — two copies of one number, and the
      // caption was the copy nobody was checking. The panel derives it
      // from the figures the API actually sent.
      return {
        label: "Trees",
        value: `🌳 ${scaled(value)}`,
        caption: "Worth of wood. Tap to see it.",
      };
    case "reams":
      return { label: "Reams", value: scaled(value), caption: "500 sheets each." };
    case "cases":
      return { label: "Cases of paper", value: `📦 ${scaled(value)}`, caption: "10 reams to a case." };
    case "water":
      return {
        label: "Water",
        value:
          value >= 1_000_000
            ? `💧 ${scaled(value / 1_000_000)}M L`
            : `💧 ${formatNumber(value)} L`,
        caption: "Used producing the paper.",
      };
    case "co2":
      return {
        label: "Carbon",
        value: value >= 1000 ? `🌫️ ${scaled(value / 1000)} kg` : `🌫️ ${scaled(value)} g`,
        caption: null,
      };
    case "miles_driven":
      return {
        label: "Same as driving",
        value: `${scaled(value)} mi`,
        caption: "In an average car.",
      };
    case "sheets_per_student":
      return {
        label: "Every student",
        value: scaled(value),
        caption: "Sheets each, if we handed it all out.",
      };
    case "gutenberg_days":
      return {
        label: "Gutenberg's press",
        value: scaled(value),
        caption: "Days it would have needed, at 250 pages a day.",
      };
    default:
      return null;
  }
}

export function PeriodPicker({
  value,
  onChange,
  disabled = false,
}: {
  value: ExplainedPeriod;
  onChange: (period: ExplainedPeriod) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="Period">
      {PERIODS.map((period) => {
        const active = period.value === value;
        return (
          <button
            key={period.value}
            type="button"
            disabled={disabled}
            aria-pressed={active}
            onClick={() => onChange(period.value)}
            className={`rounded-lg border px-3 py-1.5 text-sm transition-colors disabled:opacity-50 ${
              active
                ? "border-transparent bg-zinc-900 text-white dark:bg-white dark:text-zinc-900"
                : "border-black/[.08] text-zinc-600 hover:bg-black/[.03] dark:border-white/[.145] dark:text-zinc-400 dark:hover:bg-white/[.06]"
            }`}
          >
            {period.label}
          </button>
        );
      })}
    </div>
  );
}

/** The number is the point, so it is the biggest thing in the card and
 * the equivalency is its caption — never the other way round. */
export function StatCard({
  label,
  value,
  caption,
  accent = false,
  large = false,
}: {
  label: string;
  value: ReactNode;
  caption?: ReactNode;
  accent?: boolean;
  large?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border p-5 ${
        accent
          ? "border-transparent bg-sky-50 dark:bg-sky-950/40"
          : "border-black/[.08] bg-white dark:border-white/[.145] dark:bg-black"
      }`}
    >
      <span className="block text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      <span
        className={`mt-2 block font-semibold tabular-nums tracking-tight ${
          large ? "text-4xl sm:text-5xl" : "text-3xl"
        } ${accent ? "text-sky-700 dark:text-sky-300" : ""}`}
      >
        {value}
      </span>
      {caption ? (
        <span className="mt-2 block text-sm leading-snug text-zinc-500">
          {caption}
        </span>
      ) : null}
    </div>
  );
}

/** Progress toward the next rung. Renders nothing when the server says
 * the bar would read as broken (show_progress false) or when the ladder
 * has been topped out — in which case there is no next target and a full
 * bar would invite the question "what next?". */
export function MilestoneBar({
  milestone,
  collective,
}: {
  milestone: MilestoneProgress | null;
  collective: boolean;
}) {
  if (!milestone?.upcoming || !milestone.show_progress) return null;
  const percent = Math.round(milestone.progress * 100);
  const who = collective ? "We're" : "You're";
  return (
    <div className="mt-6 flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-sm">
        <span className="text-zinc-300">
          🚀 Next stop:{" "}
          <span className="font-semibold text-white">
            {milestone.upcoming.label}
          </span>
        </span>
        <span className="text-zinc-300">
          {who} <span className="font-semibold text-white">{percent}%</span> of
          the way
        </span>
      </div>
      <div
        className="h-2.5 overflow-hidden rounded-full bg-white/15"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Progress to ${milestone.upcoming.label}`}
      >
        <div
          className="h-full rounded-full bg-gradient-to-r from-sky-400 to-sky-200"
          style={{ width: `${Math.max(percent, 1)}%` }}
        />
      </div>
    </div>
  );
}

export function EquivalencyCards({
  equivalencies,
  sheets,
  collective,
  skip = [],
}: {
  equivalencies: Equivalency[];
  /** Needed by the trees panel so it can show its arithmetic rather than
   *  assert a result. Omit and the trees tile stays a plain tile. */
  sheets?: number;
  collective?: boolean;
  skip?: string[];
}) {
  const [openTrees, setOpenTrees] = useState(false);

  const cards = equivalencies
    .filter((e) => !skip.includes(e.key))
    .map((e) => ({ key: e.key, equivalency: e, display: describeEquivalency(e) }))
    .filter(
      (c): c is { key: string; equivalency: Equivalency; display: EquivalencyDisplay } =>
        c.display !== null,
    );

  const trees = cards.find((c) => c.key === "trees")?.equivalency;
  const treesAreOpenable = trees !== undefined && sheets !== undefined;

  return (
    <>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map((card) =>
          card.key === "trees" && treesAreOpenable ? (
            // A button rather than a click handler on the card: this opens
            // a dialog, so it has to be reachable and announced without a
            // mouse.
            <button
              key={card.key}
              type="button"
              onClick={() => setOpenTrees(true)}
              className="rounded-xl text-left transition-transform hover:-translate-y-0.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
              aria-haspopup="dialog"
            >
              <StatCard
                label={card.display.label}
                value={card.display.value}
                caption={card.display.caption}
              />
            </button>
          ) : (
            <StatCard
              key={card.key}
              label={card.display.label}
              value={card.display.value}
              caption={card.display.caption}
            />
          ),
        )}
      </div>

      {openTrees && trees !== undefined && sheets !== undefined && (
        <TreesPanel
          trees={trees.value}
          sheets={sheets}
          collective={collective ?? false}
          sourceUrl={trees.source_url}
          onClose={() => setOpenTrees(false)}
        />
      )}
    </>
  );
}
