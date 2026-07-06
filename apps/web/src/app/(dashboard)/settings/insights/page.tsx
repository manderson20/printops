"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getReportFormulaSettings,
  updateReportFormulaSettings,
  type ReportFormulaSettings,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

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
        <h2 className="text-lg font-semibold text-black dark:text-zinc-50">
          Insights
        </h2>
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
    </div>
  );
}
