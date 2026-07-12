"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getReportFormulaSettings,
  getUntrackedCopySettings,
  updateReportFormulaSettings,
  updateUntrackedCopySettings,
  type ReportFormulaSettings,
  type UntrackedCopySettings,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

const FORMULA_FIELDS: {
  key: keyof ReportFormulaSettings;
  label: string;
  step: string;
}[] = [
  {
    key: "cost_per_page_mono",
    label: "Fallback cost per mono page ($)",
    step: "0.01",
  },
  {
    key: "cost_per_page_color",
    label: "Fallback cost per color page ($)",
    step: "0.01",
  },
  {
    key: "cost_per_sheet_paper",
    label: "Cost per sheet of paper ($)",
    step: "0.001",
  },
  { key: "sheets_per_tree", label: "Sheets per tree", step: "1" },
  { key: "co2_grams_per_sheet", label: "CO₂ grams per sheet", step: "0.1" },
];

export default function InsightsSettingsPage() {
  const [settings, setSettings] = useState<ReportFormulaSettings | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const [untrackedCopySettings, setUntrackedCopySettings] =
    useState<UntrackedCopySettings | null>(null);
  const [untrackedCopyToggling, setUntrackedCopyToggling] = useState(false);
  const [untrackedCopyError, setUntrackedCopyError] = useState<string | null>(null);

  useEffect(() => {
    getReportFormulaSettings()
      .then((s) => {
        setSettings(s);
        setForm(
          Object.fromEntries(
            FORMULA_FIELDS.map(({ key }) => [key, String(s[key])]),
          ),
        );
      })
      .catch(() => setLoadFailed(true));
  }, []);

  useEffect(() => {
    getUntrackedCopySettings()
      .then(setUntrackedCopySettings)
      .catch(() => {});
  }, []);

  async function handleUntrackedCopyToggle() {
    if (!untrackedCopySettings) return;
    setUntrackedCopyToggling(true);
    setUntrackedCopyError(null);
    try {
      const updated = await updateUntrackedCopySettings(
        !untrackedCopySettings.enabled,
      );
      setUntrackedCopySettings(updated);
    } catch (err) {
      setUntrackedCopyError(
        err instanceof ApiError ? err.message : "Failed to update setting",
      );
    } finally {
      setUntrackedCopyToggling(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const input = Object.fromEntries(
        FORMULA_FIELDS.map(({ key }) => [key, Number(form[key])]),
      ) as unknown as ReportFormulaSettings;
      const updated = await updateReportFormulaSettings(input);
      setSettings(updated);
      setSaved(true);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to save formulas",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-black dark:text-zinc-50">
            Insights
          </h2>
          <WikiHelpLink page="Settings-Insights-Formula" />
        </div>
        <p className="mt-1 text-sm text-zinc-500">
          Formulas used to compute the cost/environmental estimates shown on the
          Insights report.
        </p>
      </div>

      {loadFailed && (
        <Card>
          <ErrorState>Failed to load report formulas.</ErrorState>
        </Card>
      )}
      {!settings && !loadFailed && <Spinner label="Loading…" />}
      {settings && (
        <Card>
          <CardTitle className="mb-3">Report Formulas</CardTitle>
          <p className="mb-3 text-xs text-zinc-500">
            Changes only affect future report views — saved snapshots keep
            whatever was calculated at the time.
          </p>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {FORMULA_FIELDS.map(({ key, label, step }) => (
              <label
                key={key}
                className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400"
              >
                {label}
                <Input
                  type="number"
                  step={step}
                  value={form[key] ?? ""}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, [key]: e.target.value }))
                  }
                />
              </label>
            ))}
          </div>
          {error && <ErrorState>{error}</ErrorState>}
          {saved && !error && (
            <p className="mt-2 text-xs text-emerald-700 dark:text-emerald-400">
              Saved.
            </p>
          )}
          <Button onClick={handleSave} disabled={saving} className="mt-3">
            {saving ? "Saving…" : "Save Formulas"}
          </Button>
        </Card>
      )}

      {untrackedCopySettings && (
        <Card>
          <CardTitle className="mb-3">Untracked Copy Activity</CardTitle>
          <p className="mb-3 text-xs text-zinc-500">
            Estimates walk-up copy activity PrintOps otherwise has no
            visibility into, using each printer&rsquo;s own SNMP page
            counters. For printers that report a real, vendor-broken-out
            copy counter, this is a direct measurement (&ldquo;Unattributed
            Copies&rdquo;); for printers that only report one combined
            total, it&rsquo;s an estimate — total counter growth minus the
            pages PrintOps actually printed there (&ldquo;Estimated
            Untracked Activity&rdquo;). Never attributed to a person, and
            never backfilled — only counts from the moment this is turned
            on, never retroactively.
          </p>
          <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              className="mt-1"
              checked={untrackedCopySettings.enabled}
              disabled={untrackedCopyToggling}
              onChange={handleUntrackedCopyToggle}
            />
            <span>Enabled</span>
          </label>
          {untrackedCopySettings.enabled && untrackedCopySettings.enabled_at && (
            <p className="mt-2 text-xs text-zinc-500">
              Tracking since{" "}
              {new Date(untrackedCopySettings.enabled_at).toLocaleString()}.
            </p>
          )}
          {untrackedCopyError && <ErrorState>{untrackedCopyError}</ErrorState>}
        </Card>
      )}
    </div>
  );
}
