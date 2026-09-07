"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  getReportingCalendar,
  previewReportingCalendar,
  updateReportingCalendar,
  type ReportingCalendar,
  type ReportingTerm,
  type ResolvedPeriod,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { YearsTab } from "./YearsTab";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** Starting points, not a policy. PrintOps is installed by schools, districts,
 *  businesses and libraries; picking one fills the form in so nobody has to
 *  type four rows to get going, and every field stays editable afterwards.
 *
 *  Each carries its own label style. A fiscal template that inherited a
 *  school's spanning style would number its quarters "Q1 2026 … Q3 2027" —
 *  one financial year split across two suffixes. */
const TEMPLATES: {
  name: string;
  noun: string;
  startMonth: number;
  labelStyle: "auto" | "spanning" | "single";
  terms: ReportingTerm[];
}[] = [
  {
    name: "Two semesters",
    noun: "School year",
    startMonth: 7,
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
    labelStyle: "single",
    terms: [
      { name: "Q1", start_month: 7, start_day: 1 },
      { name: "Q2", start_month: 10, start_day: 1 },
      { name: "Q3", start_month: 1, start_day: 1 },
      { name: "Q4", start_month: 4, start_day: 1 },
    ],
  },
  {
    name: "Calendar year, no segments",
    noun: "Year",
    startMonth: 1,
    labelStyle: "auto",
    terms: [],
  },
];

function formatDate(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

/** The resolver hands back an exclusive end; people read inclusive ones. */
function lastDay(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  date.setDate(date.getDate() - 1);
  return `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
}

type Tab = "year" | "segments" | "years";

export function ReportingCalendarCard() {
  const [loaded, setLoaded] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [tab, setTab] = useState<Tab>("year");

  const [yearNoun, setYearNoun] = useState("Year");
  const [startMonth, setStartMonth] = useState(1);
  const [startDay, setStartDay] = useState(1);
  const [labelStyle, setLabelStyle] = useState("auto");
  const [terms, setTerms] = useState<ReportingTerm[]>([]);

  const [preview, setPreview] = useState<ResolvedPeriod[]>([]);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // Bumped when a year override changes, so the preview below reflects it.
  const [reloadYears, setReloadYears] = useState(0);

  const apply = useCallback((next: ReportingCalendar) => {
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
    setPreview(next.preview);
  }, []);

  useEffect(() => {
    getReportingCalendar()
      .then((next) => {
        apply(next);
        setLoaded(true);
      })
      .catch(() => setLoadFailed(true));
  }, [apply]);

  // Resolve the *unsaved* form on the server, debounced. Working the dates out
  // in the browser would put a second implementation of the year and term
  // rules in TypeScript — the arrangement that had this page and the API
  // disagreeing about when a school year began.
  const sequence = useRef(0);
  useEffect(() => {
    if (!loaded) return;
    const mine = ++sequence.current;
    const timer = setTimeout(() => {
      previewReportingCalendar({
        year_start_month: startMonth,
        year_start_day: startDay,
        year_noun: yearNoun,
        label_style: labelStyle,
        terms,
      })
        .then((periods) => {
          // Out-of-order responses must not overwrite a newer one, or the
          // preview settles on whichever request happened to finish last.
          if (mine !== sequence.current) return;
          setPreview(periods);
          setPreviewError(null);
        })
        .catch((err) => {
          if (mine !== sequence.current) return;
          // A half-typed segment name or 31 February. Say so quietly here
          // rather than clearing the preview — the last good one is still the
          // most useful thing on screen.
          setPreviewError(
            err instanceof ApiError ? err.message : "This calendar cannot be resolved.",
          );
        });
    }, 350);
    return () => clearTimeout(timer);
  }, [loaded, startMonth, startDay, yearNoun, labelStyle, terms, reloadYears]);

  function updateTerm(index: number, patch: Partial<ReportingTerm>) {
    setTerms((current) =>
      current.map((term, i) => (i === index ? { ...term, ...patch } : term)),
    );
  }

  function move(index: number, by: number) {
    setTerms((current) => {
      const next = [...current];
      const target = index + by;
      if (target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      apply(
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
      setError(err instanceof ApiError ? err.message : "Failed to save the calendar.");
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
  if (!loaded) return <Spinner label="Loading…" />;

  const yearPeriod = preview.find((period) => period.kind === "year");
  const segmentPeriods = preview.filter((period) => period.kind === "term");

  const TABS: { id: Tab; label: string; hint: string }[] = [
    { id: "year", label: "Year", hint: "What a year is, and when it starts" },
    {
      id: "segments",
      label: terms.length ? `Segments · ${terms.length}` : "Segments",
      hint: "The periods within a year",
    },
    {
      id: "years",
      label: "Years",
      hint: "Every year this calendar produces, and exact dates where one differed",
    },
  ];

  return (
    <Card>
      <CardTitle className="mb-1">Reporting periods</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        A fiscal year and a school year are the same thing here — a year that
        need not start in January, divided into named segments. Call it whatever
        your organisation calls it. The calendar year stays available separately
        either way.
      </p>

      {/* Above the tabs because a template fills in both of them. */}
      <div className="mb-5 flex flex-wrap items-center gap-2 border-b border-black/[.08] pb-4 dark:border-white/[.145]">
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
        <span className="text-xs text-zinc-400">fills in both tabs</span>
      </div>

      <nav
        className="mb-5 flex gap-1 border-b border-black/[.08] dark:border-white/[.145]"
        role="tablist"
        aria-label="Reporting periods"
      >
        {TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            role="tab"
            aria-selected={tab === entry.id}
            title={entry.hint}
            onClick={() => setTab(entry.id)}
            className={`shrink-0 whitespace-nowrap border-b-2 px-3 pb-2.5 text-sm font-medium transition-colors ${
              tab === entry.id
                ? "border-accent text-accent"
                : "border-transparent text-zinc-600 hover:text-black dark:text-zinc-400 dark:hover:text-zinc-50"
            }`}
          >
            {entry.label}
          </button>
        ))}
      </nav>

      {tab === "years" ? (
        <YearsTab onChanged={() => setReloadYears((n) => n + 1)} />
      ) : tab === "year" ? (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
              A year is called
              <Input
                value={yearNoun}
                onChange={(e) => setYearNoun(e.target.value)}
                placeholder="Fiscal year"
              />
              <span className="text-xs text-zinc-500">
                Fiscal year, School year, Budget year…
              </span>
            </label>
            <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
              Starts — month
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
              Starts — day
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
                <option value="spanning">Both years — 2026–2027</option>
                <option value="single">One year — 2027</option>
              </select>
              <span className="text-xs text-zinc-500">
                A year starting in January is one number; any other start spans
                two.
              </span>
            </label>
          </div>

          {yearPeriod ? (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              Reports will say{" "}
              <span className="font-medium text-black dark:text-zinc-50">
                {yearPeriod.label}
              </span>{" "}
              for {formatDate(yearPeriod.start)} – {lastDay(yearPeriod.end)}.
            </p>
          ) : null}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex items-start justify-between gap-4">
            <p className="text-xs text-zinc-500">
              Semesters, quarters, trimesters — whatever you call them. Each runs
              until the next one begins and the last one closes the year, so only
              a start date is needed. Leave this empty if your year is not
              subdivided.
            </p>
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

          {terms.length === 0 ? (
            <p className="rounded-lg border border-dashed border-black/[.12] p-4 text-sm text-zinc-500 dark:border-white/[.15]">
              No segments. Reports will offer the year, the calendar year, this
              month and this week.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {terms.map((term, index) => (
                <div
                  key={index}
                  className="grid grid-cols-1 items-end gap-2 rounded-lg border border-black/[.06] p-2 sm:grid-cols-[1fr_auto_auto_auto] dark:border-white/[.1]"
                >
                  <label className="flex flex-col gap-1 text-xs text-zinc-500">
                    Name
                    <Input
                      value={term.name}
                      onChange={(e) => updateTerm(index, { name: e.target.value })}
                      placeholder="Q1"
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
                      className="w-20"
                      value={term.start_day}
                      onChange={(e) =>
                        updateTerm(index, { start_day: Number(e.target.value) })
                      }
                    />
                  </label>
                  <div className="flex items-center gap-1 pb-0.5">
                    {/* Order decides which segment pairs with which a year
                        earlier, so it is worth being able to set deliberately
                        rather than only by editing dates. */}
                    <button
                      type="button"
                      aria-label={`Move ${term.name || "segment"} earlier`}
                      disabled={index === 0}
                      onClick={() => move(index, -1)}
                      className="rounded border border-black/[.08] px-2 py-1.5 text-xs text-zinc-600 disabled:opacity-30 dark:border-white/[.145] dark:text-zinc-400"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      aria-label={`Move ${term.name || "segment"} later`}
                      disabled={index === terms.length - 1}
                      onClick={() => move(index, 1)}
                      className="rounded border border-black/[.08] px-2 py-1.5 text-xs text-zinc-600 disabled:opacity-30 dark:border-white/[.145] dark:text-zinc-400"
                    >
                      ↓
                    </button>
                    <Button
                      variant="secondary"
                      onClick={() =>
                        setTerms((current) => current.filter((_, i) => i !== index))
                      }
                    >
                      Remove
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Outside the Year and Segments tabs: it reflects both, and it is the
          answer to "did I just describe the year I meant". The Years tab shows
          resolved years already, so repeating one here would be noise. */}
      {tab === "years" ? null : <div className="mt-6 rounded-lg border border-black/[.08] p-3 dark:border-white/[.145]">
        <div className="flex items-baseline justify-between gap-3">
          <h4 className="text-sm font-medium text-black dark:text-zinc-50">
            This year, as configured
          </h4>
          {previewError ? (
            <span className="text-xs text-amber-600 dark:text-amber-400">
              {previewError}
            </span>
          ) : (
            <span className="text-xs text-zinc-500">Updates as you type</span>
          )}
        </div>
        {yearPeriod ? (
          <p className="mt-2 text-sm font-medium text-black dark:text-zinc-50">
            {yearPeriod.label}
            <span className="ml-2 font-normal text-zinc-500">
              {formatDate(yearPeriod.start)} – {lastDay(yearPeriod.end)}
            </span>
          </p>
        ) : null}
        {segmentPeriods.length ? (
          <ul className="mt-2 flex flex-col gap-1 text-sm text-zinc-600 dark:text-zinc-400">
            {segmentPeriods.map((period) => (
              <li key={period.key} className="flex flex-wrap gap-x-2">
                <span className="text-black dark:text-zinc-50">{period.label}</span>
                <span>
                  {formatDate(period.start)} – {lastDay(period.end)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-zinc-500">No segments.</p>
        )}
      </div>}

      {/* Saving the pattern is meaningless on the Years tab, which edits one
          year's stated dates and saves them itself. */}
      {tab === "years" ? null : (
        <div className="mt-5 flex items-center gap-3">
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save calendar"}
          </Button>
          {saved && <span className="text-sm text-green-600">Saved.</span>}
          {error && <span className="text-sm text-red-600">{error}</span>}
        </div>
      )}
    </Card>
  );
}
