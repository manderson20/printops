"use client";

import type { JobCoverage } from "@/lib/api";
import {
  INK_RATIO_EXPLAINER,
  INK_UNMEASURED_EXPLAINER,
  formatInkRatio,
  inkTone,
} from "@/lib/inkRatio";

/** How one job's measured ink compares with what its page count implies — the
 * same reading, and the same thresholds, the cost report applies to a whole
 * person or printer (lib/inkRatio.ts). */
export function InkCell({ coverage }: { coverage: JobCoverage | null }) {
  if (!coverage || coverage.ratio === null) {
    return (
      <span className="text-zinc-400" title={INK_UNMEASURED_EXPLAINER}>
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
        `\n\n${INK_RATIO_EXPLAINER}`
      }
    >
      {formatInkRatio(ratio)}
    </span>
  );
}
