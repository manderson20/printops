"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  clearReportingYearOverride,
  getReportingYears,
  overrideReportingYear,
  type ReportingSegmentOverride,
  type ReportingYear,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function readable(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

/** The resolver hands back an exclusive end; a calendar is published with an
 *  inclusive one, and that is what an admin types and reads. */
function inclusiveEnd(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  date.setDate(date.getDate() - 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate(),
  ).padStart(2, "0")}`;
}

export function YearsTab({ onChanged }: { onChanged?: () => void }) {
  const [years, setYears] = useState<ReportingYear[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<ReportingSegmentOverride[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getReportingYears()
      .then(setYears)
      .catch(() => setError("Failed to load the years."));
  }, []);

  function startEditing(year: ReportingYear) {
    setEditing(year.year);
    setDraft(
      year.segments.map((segment) => ({
        name: segment.label.replace(/\s+\S*\d{4}\S*$/, ""),
        start_date: segment.start,
        end_date: inclusiveEnd(segment.end),
      })),
    );
  }

  async function save(year: number) {
    setBusy(true);
    setError(null);
    try {
      setYears(await overrideReportingYear(year, draft));
      setEditing(null);
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save those dates.");
    } finally {
      setBusy(false);
    }
  }

  async function revert(year: number) {
    setBusy(true);
    setError(null);
    try {
      setYears(await clearReportingYearOverride(year));
      setEditing(null);
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to revert that year.");
    } finally {
      setBusy(false);
    }
  }

  if (error && !years) return <p className="text-sm text-red-600">{error}</p>;
  if (!years) return <Spinner label="Loading…" />;

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-zinc-500">
        Every year your calendar produces, generated from the segments you
        defined — nothing is stored per year, so next year needs no setting up
        and years from before PrintOps was installed still resolve. Override a
        year only where its real dates differed.
      </p>
      {error ? <p className="text-sm text-red-600">{error}</p> : null}

      <div className="flex flex-col gap-2">
        {years.map((year) => (
          <div
            key={year.year}
            className="rounded-lg border border-black/[.08] p-3 dark:border-white/[.145]"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-sm font-medium text-black dark:text-zinc-50">
                  {year.label}
                </span>
                <span className="text-xs text-zinc-500">
                  {readable(year.start)} – {readable(inclusiveEnd(year.end))}
                </span>
                {year.overridden ? (
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                    dates set manually
                  </span>
                ) : null}
                {!year.has_data ? (
                  <span
                    className="rounded bg-black/[.05] px-1.5 py-0.5 text-[11px] text-zinc-500 dark:bg-white/[.08]"
                    title="This year ended before PrintOps recorded anything, so reports for it will be empty."
                  >
                    no data collected
                  </span>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                {year.overridden ? (
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() => revert(year.year)}
                  >
                    Use the pattern
                  </Button>
                ) : null}
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() =>
                    editing === year.year ? setEditing(null) : startEditing(year)
                  }
                >
                  {editing === year.year ? "Cancel" : "Set exact dates"}
                </Button>
              </div>
            </div>

            {editing === year.year ? (
              <div className="mt-3 flex flex-col gap-2 border-t border-black/[.06] pt-3 dark:border-white/[.1]">
                <p className="text-xs text-zinc-500">
                  Stating dates replaces this year entirely; every other year
                  stays on the pattern. End dates are the last day the segment
                  covers.
                </p>
                {draft.map((segment, index) => (
                  <div
                    key={index}
                    className="grid grid-cols-1 items-end gap-2 sm:grid-cols-[1fr_auto_auto_auto]"
                  >
                    <label className="flex flex-col gap-1 text-xs text-zinc-500">
                      Name
                      <Input
                        value={segment.name}
                        onChange={(e) =>
                          setDraft((current) =>
                            current.map((item, i) =>
                              i === index ? { ...item, name: e.target.value } : item,
                            ),
                          )
                        }
                      />
                    </label>
                    <label className="flex flex-col gap-1 text-xs text-zinc-500">
                      First day
                      <Input
                        type="date"
                        value={segment.start_date}
                        onChange={(e) =>
                          setDraft((current) =>
                            current.map((item, i) =>
                              i === index ? { ...item, start_date: e.target.value } : item,
                            ),
                          )
                        }
                      />
                    </label>
                    <label className="flex flex-col gap-1 text-xs text-zinc-500">
                      Last day
                      <Input
                        type="date"
                        value={segment.end_date}
                        onChange={(e) =>
                          setDraft((current) =>
                            current.map((item, i) =>
                              i === index ? { ...item, end_date: e.target.value } : item,
                            ),
                          )
                        }
                      />
                    </label>
                    <Button
                      variant="secondary"
                      disabled={busy}
                      onClick={() =>
                        setDraft((current) => current.filter((_, i) => i !== index))
                      }
                    >
                      Remove
                    </Button>
                  </div>
                ))}
                <div className="flex items-center gap-2">
                  <Button disabled={busy || draft.length === 0} onClick={() => save(year.year)}>
                    {busy ? "Saving…" : `Save ${year.label}`}
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() =>
                      setDraft((current) => [
                        ...current,
                        { name: "", start_date: year.start, end_date: year.start },
                      ])
                    }
                  >
                    Add a segment
                  </Button>
                </div>
              </div>
            ) : (
              <ul className="mt-2 flex flex-col gap-0.5 text-sm text-zinc-600 dark:text-zinc-400">
                {year.segments.length === 0 ? (
                  <li className="text-zinc-500">No segments.</li>
                ) : (
                  year.segments.map((segment) => (
                    <li key={segment.key} className="flex flex-wrap gap-x-2">
                      <span className="text-black dark:text-zinc-50">{segment.label}</span>
                      <span>
                        {readable(segment.start)} – {readable(inclusiveEnd(segment.end))}
                      </span>
                    </li>
                  ))
                )}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
