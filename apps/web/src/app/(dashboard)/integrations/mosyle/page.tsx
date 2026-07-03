"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  getMosyleSettings,
  syncMosyleDevices,
  testMosyleConnection,
  updateMosyleSettings,
  type MosyleSettings,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { PasswordField } from "@/components/ui/PasswordField";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; settings: MosyleSettings }
  | { phase: "error"; message: string };

export default function MosyleSettingsPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
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

  useEffect(() => {
    if (currentUser && currentUser.role !== "admin") {
      router.replace("/integrations");
    }
  }, [currentUser, router]);

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

  if (state.phase === "loading" || currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading settings…" />;
  }
  if (state.phase === "error") {
    return <ErrorState>{state.message}</ErrorState>;
  }

  const { settings } = state;
  const readyToTest = Boolean(
    form.access_token || settings.has_access_token,
  ) && Boolean(form.admin_email) && Boolean(form.admin_password || settings.has_admin_password);

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Mosyle Integration</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Used for real print-job attribution via device→user lookup (see the Jobs page) instead
          of relying on whatever CUPS reports. This deployment uses{" "}
          <strong className="font-medium text-zinc-700 dark:text-zinc-300">Mosyle Manager</strong>{" "}
          (the K-12 schools product) — not Mosyle Business, a different product with a different
          API host.
        </p>
      </div>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            1
          </span>
          <CardTitle className="mb-0">API Access Token</CardTitle>
        </div>
        <p className="mb-4 ml-7 text-xs text-zinc-500">
          In Mosyle: <strong className="font-medium">My School → Integrations → API</strong> (wording
          may vary slightly) → activate/add a new integration token, then paste it below.
        </p>
        <div className="ml-7 flex flex-col gap-4">
          <Field label="Base URL">
            <Input value={form.base_url} onChange={(e) => update("base_url", e.target.value)} />
          </Field>
          <PasswordField
            label={
              <>
                Access Token{" "}
                {settings.has_access_token && (
                  <span className="text-xs text-zinc-500">(already set — leave blank to keep)</span>
                )}
              </>
            }
            value={form.access_token}
            onChange={(v) => update("access_token", v)}
            placeholder={settings.has_access_token ? "•••••••• (unchanged)" : ""}
          />
        </div>
      </Card>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            2
          </span>
          <CardTitle className="mb-0">Admin Login</CardTitle>
        </div>
        <p className="mb-4 ml-7 text-xs text-zinc-500">
          Mosyle requires an admin&apos;s own login credentials <em>in addition to</em> the access
          token above (not a separate API-only account) — this is Mosyle&apos;s requirement, not a
          PrintOps design choice.
        </p>
        <div className="ml-7 flex flex-col gap-4">
          <Field label="Admin Email">
            <Input
              type="email"
              value={form.admin_email}
              onChange={(e) => update("admin_email", e.target.value)}
            />
          </Field>
          <PasswordField
            label={
              <>
                Admin Password{" "}
                {settings.has_admin_password && (
                  <span className="text-xs text-zinc-500">(already set — leave blank to keep)</span>
                )}
              </>
            }
            value={form.admin_password}
            onChange={(v) => update("admin_password", v)}
            placeholder={settings.has_admin_password ? "•••••••• (unchanged)" : ""}
          />
        </div>
      </Card>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            3
          </span>
          <CardTitle className="mb-0">Test &amp; Save</CardTitle>
        </div>
        <p className="mb-4 ml-7 text-xs text-zinc-500">
          Test Connection checks whatever&apos;s currently in the fields above — including a
          just-typed token — before you save anything, so a mistake shows up here first.
        </p>

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
                Off = jobs are never looked up in Mosyle, even if credentials are configured.
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
          {!readyToTest && !testResult && (
            <p className="text-xs text-zinc-400">Fill in the token and admin login above to test.</p>
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
