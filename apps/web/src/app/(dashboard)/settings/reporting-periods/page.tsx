"use client";

import { ReportingCalendarCard } from "./ReportingCalendarCard";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

export default function ReportingPeriodsSettingsPage() {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-black dark:text-zinc-50">
            Reporting Periods
          </h2>
          <WikiHelpLink page="Settings-Reporting-Periods" />
        </div>
        <p className="mt-1 text-sm text-zinc-500">
          The periods every report is grouped and compared by — what your
          organisation calls a year, when it starts, and the segments within it.
          A fiscal year and a school year are the same thing here; only the name
          differs.
        </p>
      </div>

      <ReportingCalendarCard />
    </div>
  );
}
