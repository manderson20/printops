"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getQuotaSettings,
  updateQuotaSettings,
  type QuotaSettings,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; settings: QuotaSettings }
  | { phase: "error"; message: string };

export default function QuotaSettingsPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [enabled, setEnabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function load() {
    getQuotaSettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setEnabled(settings.enabled);
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message:
            error instanceof Error
              ? error.message
              : "Failed to load quota settings",
        }),
      );
  }

  useEffect(load, []);

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      const settings = await updateQuotaSettings({ enabled });
      setState({ phase: "ok", settings });
    } catch (err) {
      setSaveError(
        err instanceof ApiError ? err.message : "Failed to save quota settings",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-1">Page Quotas</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Org-wide kill switch for per-printer, per-user page quotas. Individual
        limits are configured on each printer&rsquo;s own detail page (a Quotas
        panel there). Turning this off does not delete any configured limits —
        it just stops enforcing them, so a misconfigured limit never holds a
        real job until you&rsquo;ve confirmed it&rsquo;s right.
      </p>

      <p className="mb-4 text-xs text-zinc-500">
        There are two other, independent ways a job can be held instead of
        printing immediately — <strong>Print Release</strong> (a PIN kiosk at
        one specific printer) and <strong>Follow-Me Printing</strong>
        (releasable at any printer that&rsquo;s opted in, including a virtual
        Follow-Me queue with no physical printer of its own). Neither has a
        global setting here — both are turned on per printer, on that
        printer&rsquo;s own Release &amp; Quotas tab.
      </p>

      {state.phase === "loading" && <Spinner label="Loading…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase !== "loading" && (
        <div className="flex flex-col gap-4">
          <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              className="mt-1"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            <span>
              Enforce page quotas
              <br />
              <span className="text-xs text-zinc-500">
                Off = quotas can be configured and usage tracked, but no job is
                ever held for being over a limit.
              </span>
            </span>
          </label>

          {saveError && <ErrorState>{saveError}</ErrorState>}
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      )}
    </Card>
  );
}
