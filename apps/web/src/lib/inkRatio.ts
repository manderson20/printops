/** How a measured ink ratio is read, in one place.
 *
 * 1.0 is a page just like the manufacturer's test page, which is what a rated
 * yield — and therefore every cost PrintOps reported before measurement — was
 * quoting. The interesting jobs are the ones far from it: on a real estate they
 * ranged from 0.07 to 8.8, all of them charged identically by a flat per-page
 * rate.
 *
 * The threshold and the rounding live here rather than in each screen because
 * a job and the person who sent it have to agree about what counts as heavy.
 * This codebase's recurring defect is one rule written down twice.
 */

/** Only the extremes are coloured. A table where most rows are tinted teaches
 * people to stop seeing the colour, and most jobs sit near 1.0. */
export function inkTone(ratio: number): string {
  if (ratio >= 2) return "text-amber-700 dark:text-amber-400";
  if (ratio <= 0.5) return "text-emerald-700 dark:text-emerald-400";
  return "text-zinc-600 dark:text-zinc-400";
}

/** Two significant figures below ten: "0.07x" and "8.8x" both matter, and
 * "0.1x" would round away the difference between a nearly blank page and a
 * light one. */
export function formatInkRatio(ratio: number): string {
  return `${ratio < 10 ? ratio.toFixed(2) : ratio.toFixed(0)}×`;
}

export const INK_RATIO_EXPLAINER =
  "1.0× is a page like the manufacturer's test page, which is what the" +
  " rated cost assumes.";

/** What a dash in an ink column means. Every one of these reasons is a job
 * whose ink is unknown — never a job that used none. */
export const INK_UNMEASURED_EXPLAINER =
  "Not measured. The document may not have been read yet, its spooled copy" +
  " may have aged out, or this may be a copy — walk-up copying produces no" +
  " document to measure.";
