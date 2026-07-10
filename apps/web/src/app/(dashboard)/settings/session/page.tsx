"use client";

import { useEffect, useState } from "react";
import { ApiError, getSessionSettings, updateSessionSettings, type SessionSettings } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; settings: SessionSettings }
  | { phase: "error"; message: string };

export default function SessionSettingsPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [idleTimeoutMinutes, setIdleTimeoutMinutes] = useState("60");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function load() {
    getSessionSettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setIdleTimeoutMinutes(String(settings.idle_timeout_minutes));
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load session settings",
        }),
      );
  }

  useEffect(load, []);

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      const settings = await updateSessionSettings({
        idle_timeout_minutes: Number(idleTimeoutMinutes),
      });
      setState({ phase: "ok", settings });
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Failed to save session settings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-1">Session Timeout</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        How long a signed-in session can sit idle before it&rsquo;s signed out. This is idle time,
        not a flat cutoff from login — the browser tab quietly extends the session every couple of
        minutes while you&rsquo;re actually using it, so only genuine inactivity for this long logs
        you out. To exempt a specific person from timing out entirely (e.g. a shared front-desk
        login), use the &ldquo;No timeout&rdquo; toggle on that account in Settings → Users.
      </p>

      {state.phase === "loading" && <Spinner label="Loading…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase !== "loading" && (
        <div className="flex flex-col gap-4">
          <Field label="Idle timeout (minutes)">
            <Input
              type="number"
              min="1"
              value={idleTimeoutMinutes}
              onChange={(e) => setIdleTimeoutMinutes(e.target.value)}
              className="max-w-[10rem]"
            />
          </Field>

          {saveError && <ErrorState>{saveError}</ErrorState>}
          <Button onClick={handleSave} disabled={saving} className="self-start">
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      )}
    </Card>
  );
}
