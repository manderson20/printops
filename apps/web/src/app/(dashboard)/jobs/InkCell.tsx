"use client";

import type { JobCoverage } from "@/lib/api";

/** How a job's measured ink compares with what its page count implies.
 *
 * 1.0 is a page just like the manufacturer's test page, which is what a rated
 * yield — and therefore every cost PrintOps reported before measurement — was
 * quoting. The interesting jobs are the ones far from it: on a real estate
 * they ranged from 0.07 to 8.8, all of them charged identically by a flat
 * per-page rate.
 *
 * Only the extremes are coloured. A table where most rows are tinted teaches
 * people to stop seeing the colour, and most jobs sit near 1.0.
 */
function inkTone(ratio: number): string {
  if (ratio >= 2) return "text-amber-700 dark:text-amber-400";
  if (ratio <= 0.5) return "text-emerald-700 dark:text-emerald-400";
  return "text-zinc-600 dark:text-zinc-400";
}

export function InkCell({ coverage }: { coverage: JobCoverage | null }) {
  if (!coverage || coverage.ratio === null) {
    return (
      <span
        className="text-zinc-400"
        title={
          "Not measured. The document may not have been read yet, its spooled " +
          "copy may have aged out, or this may be a copy — walk-up copying " +
          "produces no document to measure."
        }
      >
        —
      </span>
    );
  }

  const { ratio } = coverage;
  const channels = [
    ["C", coverage.cyan],
    ["M", coverage.magenta],
    ["Y", coverage.yellow],
    ["K", coverage.black],
  ] as [string, number][];

  return (
    <span
      className={inkTone(ratio)}
      title={
        `${channels.map(([name, v]) => `${name} ${(v * 100).toFixed(1)}%`).join("  ")}` +
        `\nover ${coverage.pages_measured} page${coverage.pages_measured === 1 ? "" : "s"}` +
        `\n\n1.0x is a page like the manufacturer's test page, which is what the` +
        ` rated cost assumes.`
      }
    >
      {/* Two significant figures below ten: "0.07x" and "8.8x" both matter,
          and "0.1x" would round away the difference between a nearly blank
          page and a light one. */}
      {ratio < 10 ? ratio.toFixed(2) : ratio.toFixed(0)}&times;
    </span>
  );
}
