"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getMosyleSettings,
  syncMosyleDevices,
  testMosyleConnection,
  updateMosyleSettings,
  type MosyleSettings,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; settings: MosyleSettings }
  | { phase: "error"; message: string };

export default function MosyleSettingsPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [form, setForm] = useState({
    base_url: "",
    access_token: "",
    admin_email: "",
    admin_password: "",
  });
  const [enabled, setEnabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    getMosyleSettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setForm((prev) => ({ ...prev, base_url: settings.base_url, admin_email: settings.admin_email ?? "" }));
        setEnabled(settings.enabled);
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load settings",
        }),
      );
  }, []);

  function update(field: "base_url" | "access_token" | "admin_email" | "admin_password", value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      const settings = await updateMosyleSettings({
        base_url: form.base_url || undefined,
        access_token: form.access_token || undefined,
        admin_email: form.admin_email || undefined,
        admin_password: form.admin_password || undefined,
        enabled,
      });
      setState({ phase: "ok", settings });
      setForm((prev) => ({ ...prev, access_token: "", admin_password: "" }));
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    setActionError(null);
    setTestResult(null);
    try {
      const result = await testMosyleConnection({
        base_url: form.base_url || undefined,
        access_token: form.access_token || undefined,
        admin_email: form.admin_email || undefined,
        admin_password: form.admin_password || undefined,
      });
      setTestResult(
        result.ok
          ? { ok: true, message: `Connected — ${result.device_count} device(s) found.` }
          : { ok: false, message: result.error ?? "Connection failed." },
      );
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof ApiError ? err.message : "Connection test failed" });
    } finally {
      setTesting(false);
    }
  }

  async function handleSync() {
    setSyncing(true);
    setActionError(null);
    try {
      const settings = await syncMosyleDevices();
      setState({ phase: "ok", settings });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  if (state.phase === "loading") {
    return <Spinner label="Loading settings…" />;
  }
  if (state.phase === "error") {
    return <ErrorState>{state.message}</ErrorState>;
  }

  const { settings } = state;

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Mosyle Integration</h1>

      <Card>
        <CardTitle className="mb-1">Connection</CardTitle>
        <p className="mb-4 text-xs text-zinc-500">
          Used to resolve real print-job attribution via device→user lookup (see the Jobs page)
          instead of relying on whatever CUPS reports. Access token and admin password are stored
          encrypted and never shown again after saving — leave them blank on an edit to keep the
          existing value.
        </p>

        <div className="flex flex-col gap-4">
          <Field label="Base URL">
            <Input value={form.base_url} onChange={(e) => update("base_url", e.target.value)} />
          </Field>
          <Field label="Admin Email">
            <Input
              type="email"
              value={form.admin_email}
              onChange={(e) => update("admin_email", e.target.value)}
            />
          </Field>
          <Field
            label={
              <>
                Access Token{" "}
                {settings.has_access_token && (
                  <span className="text-xs text-zinc-500">(already set — leave blank to keep)</span>
                )}
              </>
            }
          >
            <Input
              type="password"
              value={form.access_token}
              onChange={(e) => update("access_token", e.target.value)}
              placeholder={settings.has_access_token ? "••••••••" : ""}
            />
          </Field>
          <Field
            label={
              <>
                Admin Password{" "}
                {settings.has_admin_password && (
                  <span className="text-xs text-zinc-500">(already set — leave blank to keep)</span>
                )}
              </>
            }
          >
            <Input
              type="password"
              value={form.admin_password}
              onChange={(e) => update("admin_password", e.target.value)}
              placeholder={settings.has_admin_password ? "••••••••" : ""}
            />
          </Field>

          <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
            <input type="checkbox" className="mt-1" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            <span>
              Enabled
              <br />
              <span className="text-xs text-zinc-500">
                Off = jobs are never looked up in Mosyle, even if credentials are configured.
              </span>
            </span>
          </label>
        </div>

        {actionError && <ErrorState>{actionError}</ErrorState>}
        {testResult && (
          <p className={`mt-3 text-sm ${testResult.ok ? "text-emerald-700 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
            {testResult.message}
          </p>
        )}

        <div className="mt-4 flex gap-3">
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
          <Button variant="secondary" onClick={handleTest} disabled={testing}>
            {testing ? "Testing…" : "Test Connection"}
          </Button>
        </div>
      </Card>

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <CardTitle>Device Cache</CardTitle>
          <Button variant="secondary" onClick={handleSync} disabled={syncing}>
            {syncing ? "Syncing…" : "Sync Now"}
          </Button>
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm text-zinc-600 dark:text-zinc-400">
          <dt className="font-medium text-zinc-500">Status</dt>
          <dd>
            {settings.enabled ? <Badge tone="success">Enabled</Badge> : <Badge tone="neutral">Disabled</Badge>}
          </dd>
          <dt className="font-medium text-zinc-500">Cached Devices</dt>
          <dd>{settings.device_count}</dd>
          <dt className="font-medium text-zinc-500">Last Synced</dt>
          <dd>{settings.last_synced_at ? new Date(settings.last_synced_at).toLocaleString() : "Never"}</dd>
        </dl>

        {settings.last_sync_error && (
          <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
            <p className="font-medium">Last sync failed.</p>
            <p className="mt-1 text-amber-800 dark:text-amber-300">{settings.last_sync_error}</p>
          </div>
        )}
      </Card>
    </div>
  );
}
