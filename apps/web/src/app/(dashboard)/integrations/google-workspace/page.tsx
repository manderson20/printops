"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getGoogleWorkspaceSettings,
  syncGoogleWorkspaceDevices,
  testGoogleWorkspaceConnection,
  updateGoogleWorkspaceSettings,
  type GoogleWorkspaceSettings,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; settings: GoogleWorkspaceSettings }
  | { phase: "error"; message: string };

export default function GoogleWorkspaceSettingsPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [form, setForm] = useState({
    service_account_json: "",
    admin_email: "",
    customer_id: "",
  });
  const [enabled, setEnabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    getGoogleWorkspaceSettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setForm((prev) => ({
          ...prev,
          admin_email: settings.admin_email ?? "",
          customer_id: settings.customer_id,
        }));
        setEnabled(settings.enabled);
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load settings",
        }),
      );
  }, []);

  function update(field: "service_account_json" | "admin_email" | "customer_id", value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      const settings = await updateGoogleWorkspaceSettings({
        service_account_json: form.service_account_json || undefined,
        admin_email: form.admin_email || undefined,
        customer_id: form.customer_id || undefined,
        enabled,
      });
      setState({ phase: "ok", settings });
      setForm((prev) => ({ ...prev, service_account_json: "" }));
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
      const result = await testGoogleWorkspaceConnection({
        service_account_json: form.service_account_json || undefined,
        admin_email: form.admin_email || undefined,
        customer_id: form.customer_id || undefined,
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
      const settings = await syncGoogleWorkspaceDevices();
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
  const readyToTest = Boolean((form.service_account_json || settings.has_service_account_json) && form.admin_email);

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Google Workspace Integration</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Resolves ChromeOS device→user attribution for print jobs (strategy 3, tried after Mosyle
          — see the Jobs page). Auth is a Google service account with domain-wide delegation, not a
          simple token.
        </p>
      </div>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            1
          </span>
          <CardTitle className="mb-0">Service Account</CardTitle>
        </div>
        <p className="mb-4 ml-7 text-xs text-zinc-500">
          In Google Cloud Console: create a service account, generate a JSON key, and paste its
          full contents below. Then, in Google Admin Console → Security → API Controls → Domain-wide
          Delegation, authorize that service account&apos;s numeric Client ID for the scope{" "}
          <code className="text-[11px]">admin.directory.device.chromeos.readonly</code>.
        </p>
        <div className="ml-7 flex flex-col gap-4">
          <Field
            label={
              <>
                Service Account JSON Key{" "}
                {settings.has_service_account_json && (
                  <span className="text-xs text-zinc-500">(already set — leave blank to keep)</span>
                )}
              </>
            }
          >
            <Textarea
              rows={6}
              value={form.service_account_json}
              onChange={(e) => update("service_account_json", e.target.value)}
              placeholder={settings.has_service_account_json ? "•••••••• (unchanged)" : '{"type": "service_account", ...}'}
              className="font-mono text-xs"
            />
          </Field>
        </div>
      </Card>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            2
          </span>
          <CardTitle className="mb-0">Delegation</CardTitle>
        </div>
        <p className="mb-4 ml-7 text-xs text-zinc-500">
          The Workspace admin/user the service account impersonates — needs directory-read
          permission. This is who the API calls are made "as," not a secret itself.
        </p>
        <div className="ml-7 flex flex-col gap-4">
          <Field label="Admin Email">
            <Input
              type="email"
              value={form.admin_email}
              onChange={(e) => update("admin_email", e.target.value)}
              placeholder="admin@yourdomain.org"
            />
          </Field>
          <Field label="Customer ID">
            <Input value={form.customer_id} onChange={(e) => update("customer_id", e.target.value)} />
            <span className="text-xs text-zinc-500">
              Leave as <code className="text-[11px]">my_customer</code> unless Google gave you a
              specific customer ID.
            </span>
          </Field>
        </div>
      </Card>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            3
          </span>
          <CardTitle className="mb-0">Test &amp; Save</CardTitle>
        </div>

        <div className="ml-7 flex flex-col gap-4">
          <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              className="mt-1"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            <span>
              Enabled
              <br />
              <span className="text-xs text-zinc-500">
                Off = jobs are never looked up in Google Workspace, even if credentials are
                configured.
              </span>
            </span>
          </label>

          {actionError && <ErrorState>{actionError}</ErrorState>}
          {testResult && (
            <p
              className={`text-sm ${testResult.ok ? "text-emerald-700 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}
            >
              {testResult.ok ? "✓ " : "✗ "}
              {testResult.message}
            </p>
          )}

          <div className="flex gap-3">
            <Button variant="secondary" onClick={handleTest} disabled={testing || !readyToTest}>
              {testing ? "Testing…" : "Test Connection"}
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
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
