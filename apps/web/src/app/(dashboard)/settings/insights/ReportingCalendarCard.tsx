"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getReportingCalendar,
  updateReportingCalendar,
  type ReportingCalendar,
  type ReportingTerm,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** Starting points, not a policy. PrintOps is installed by schools, districts,
 *  businesses and libraries; picking one of these fills the form in so nobody
 *  has to type four rows to get going, and every field stays editable
 *  afterwards. The neutral default remains "Year" with no terms — an
 *  organisation that does not subdivide its year is a supported shape, not an
 *  unconfigured one. */
const TEMPLATES: {
  name: string;
  noun: string;
  startMonth: number;
  /** Set explicitly per template, never left at whatever was loaded before.
   *  The label style decides how terms are numbered, so a fiscal template that
   *  inherited a school's spanning style would label its four quarters
   *  "Q1 2026, Q2 2026, Q3 2027, Q4 2027" — one financial year split across
   *  two suffixes, which is the exact thing the labelling rules exist to
   *  prevent. */
  labelStyle: "auto" | "spanning" | "single";
  terms: ReportingTerm[];
}[] = [
  {
    name: "Two semesters",
    noun: "School year",
    startMonth: 7,
    // A school year spans two calendar years and is named for both, and its
    // terms carry their own year: "Fall 2026", "Spring 2027".
    labelStyle: "spanning",
    terms: [
      { name: "Fall Semester", start_month: 8, start_day: 15 },
      { name: "Spring Semester", start_month: 1, start_day: 5 },
    ],
  },
  {
    name: "Three trimesters",
    noun: "School year",
    startMonth: 7,
    labelStyle: "spanning",
    terms: [
      { name: "Trimester 1", start_month: 8, start_day: 15 },
      { name: "Trimester 2", start_month: 11, start_day: 15 },
      { name: "Trimester 3", start_month: 3, start_day: 1 },
    ],
  },
  {
    name: "Four quarters",
    noun: "Fiscal year",
    startMonth: 7,
    // One number, the year it ends in — so all four quarters read as the same
    // fiscal year rather than splitting across two.
    labelStyle: "single",
    terms: [
      { name: "Q1", start_month: 7, start_day: 1 },
      { name: "Q2", start_month: 10, start_day: 1 },
      { name: "Q3", start_month: 1, start_day: 1 },
      { name: "Q4", start_month: 4, start_day: 1 },
    ],
  },
  { name: "No terms", noun: "Year", startMonth: 1, labelStyle: "auto", terms: [] },
];

function formatDate(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

/** The preview sends back an exclusive end; people read inclusive ones. */
function lastDay(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  date.setDate(date.getDate() - 1);
  return formatDate(
    `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
      date.getDate(),
    ).padStart(2, "0")}`,
  );
}

export function ReportingCalendarCard() {
  const [calendar, setCalendar] = useState<ReportingCalendar | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [yearNoun, setYearNoun] = useState("Year");
  const [startMonth, setStartMonth] = useState(7);
  const [startDay, setStartDay] = useState(1);
  const [labelStyle, setLabelStyle] = useState("auto");
  const [terms, setTerms] = useState<ReportingTerm[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  function load(next: ReportingCalendar) {
    setCalendar(next);
    setYearNoun(next.year_noun);
    setStartMonth(next.year_start_month);
    setStartDay(next.year_start_day);
    setLabelStyle(next.label_style);
    setTerms(
      next.terms.map((term) => ({
        name: term.name,
        start_month: term.start_month,
        start_day: term.start_day,
      })),
    );
  }

  useEffect(() => {
    getReportingCalendar()
      .then(load)
      .catch(() => setLoadFailed(true));
  }, []);

  function updateTerm(index: number, patch: Partial<ReportingTerm>) {
    setTerms((current) =>
      current.map((term, i) => (i === index ? { ...term, ...patch } : term)),
    );
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      load(
        await updateReportingCalendar({
          year_start_month: startMonth,
          year_start_day: startDay,
          year_noun: yearNoun,
          label_style: labelStyle,
          terms,
        }),
      );
      setSaved(true);
    } catch (err) {
      // The server rejects 31 February and duplicate terms; showing its own
      // message says which field is wrong instead of "save failed".
      setError(
        err instanceof ApiError ? err.message : "Failed to save the calendar.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (loadFailed) {
    return (
      <Card>
        <ErrorState>Failed to load the reporting calendar.</ErrorState>
      </Card>
    );
  }
  if (!calendar) return <Spinner label="Loading…" />;

  return (
    <Card>
      <CardTitle className="mb-1">Reporting periods</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        What this organisation calls a year, when it starts, and the segments it
        divides into. A school year and a fiscal year are the same thing here —
        only the name differs. Terms are optional: leave the list empty if your
        year is not subdivided. The calendar year is always available
        separately.
      </p>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-xs text-zinc-500">Start from:</span>
        {TEMPLATES.map((template) => (
          <button
            key={template.name}
            type="button"
            onClick={() => {
              setYearNoun(template.noun);
              setStartMonth(template.startMonth);
              setStartDay(1);
              setLabelStyle(template.labelStyle);
              setTerms(template.terms.map((term) => ({ ...term })));
            }}
            className="rounded-lg border border-black/[.08] px-2.5 py-1 text-xs text-zinc-600 hover:bg-black/[.03] dark:border-white/[.145] dark:text-zinc-400 dark:hover:bg-white/[.06]"
          >
            {template.name}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
          A year is called
          <Input
            value={yearNoun}
            onChange={(e) => setYearNoun(e.target.value)}
            placeholder="School year"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
          Year starts — month
          <select
            value={startMonth}
            onChange={(e) => setStartMonth(Number(e.target.value))}
            className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
          >
            {MONTHS.map((month, index) => (
              <option key={month} value={index + 1}>
                {month}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
          Year starts — day
          <Input
            type="number"
            min={1}
            max={31}
            value={startDay}
            onChange={(e) => setStartDay(Number(e.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
          Labelled as
          <select
            value={labelStyle}
            onChange={(e) => setLabelStyle(e.target.value)}
            className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
          >
            <option value="auto">Automatic</option>
            <option value="spanning">Both years (2026–2027)</option>
            <option value="single">One year (2027)</option>
          </select>
        </label>
      </div>

      <div className="mt-5">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-medium text-black dark:text-zinc-50">
            Segments of the year
          </h4>
          <Button
            variant="secondary"
            onClick={() =>
              setTerms((current) => [
                ...current,
                { name: "", start_month: 1, start_day: 1 },
              ])
            }
          >
            Add a segment
          </Button>
        </div>
        <p className="mt-1 text-xs text-zinc-500">
          Semesters, quarters, trimesters — whatever you call them. Each runs
          until the next one begins, and the last one closes the year, so only a
          start date is needed.
        </p>

        {terms.length === 0 ? (
          <p className="mt-3 text-sm text-zinc-500">
            No segments. Reports will offer the year, the calendar year, this
            month and this week.
          </p>
        ) : (
          <div className="mt-3 flex flex-col gap-2">
            {terms.map((term, index) => (
              <div
                key={index}
                className="grid grid-cols-1 items-end gap-2 sm:grid-cols-[1fr_auto_auto_auto]"
              >
                <label className="flex flex-col gap-1 text-xs text-zinc-500">
                  Name
                  <Input
                    value={term.name}
                    onChange={(e) => updateTerm(index, { name: e.target.value })}
                    placeholder="Fall Semester"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-zinc-500">
                  Starts — month
                  <select
                    value={term.start_month}
                    onChange={(e) =>
                      updateTerm(index, { start_month: Number(e.target.value) })
                    }
                    className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
                  >
                    {MONTHS.map((month, monthIndex) => (
                      <option key={month} value={monthIndex + 1}>
                        {month}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-xs text-zinc-500">
                  Day
                  <Input
                    type="number"
                    min={1}
                    max={31}
                    value={term.start_day}
                    onChange={(e) =>
                      updateTerm(index, { start_day: Number(e.target.value) })
                    }
                    className="w-20"
                  />
                </label>
                <Button
                  variant="secondary"
                  onClick={() =>
                    setTerms((current) => current.filter((_, i) => i !== index))
                  }
                >
                  Remove
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {calendar.preview.length > 0 && (
        <div className="mt-5 rounded-lg border border-black/[.08] p-3 dark:border-white/[.145]">
          <h4 className="text-sm font-medium text-black dark:text-zinc-50">
            This year, as saved
          </h4>
          <p className="mt-1 text-xs text-zinc-500">
            What the saved settings resolve to right now. Save to update it.
          </p>
          <ul className="mt-2 flex flex-col gap-1 text-sm text-zinc-600 dark:text-zinc-400">
            {calendar.preview.map((period) => (
              <li key={period.key} className="flex flex-wrap gap-x-2">
                <span className="font-medium text-black dark:text-zinc-50">
                  {period.label}
                </span>
                <span>
                  {formatDate(period.start)} – {lastDay(period.end)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-5 flex items-center gap-3">
        <Button onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save calendar"}
        </Button>
        {saved && <span className="text-sm text-green-600">Saved.</span>}
        {error && <span className="text-sm text-red-600">{error}</span>}
      </div>
    </Card>
  );
}
