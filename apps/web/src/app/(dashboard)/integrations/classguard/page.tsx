"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  getClassGuardSettings,
  testClassGuardConnection,
  updateClassGuardSettings,
  type ClassGuardSettings,
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
  | { phase: "ok"; settings: ClassGuardSettings }
  | { phase: "error"; message: string };

export default function ClassGuardSettingsPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [form, setForm] = useState({ base_url: "", access_token: "" });
  const [testIp, setTestIp] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    getClassGuardSettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setForm((prev) => ({ ...prev, base_url: settings.base_url }));
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

  function update(field: "base_url" | "access_token", value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      const settings = await updateClassGuardSettings({
        base_url: form.base_url || undefined,
        access_token: form.access_token || undefined,
        enabled,
      });
      setState({ phase: "ok", settings });
      setForm((prev) => ({ ...prev, access_token: "" }));
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    if (!testIp) return;
    setTesting(true);
    setActionError(null);
    setTestResult(null);
    try {
      const result = await testClassGuardConnection({
        base_url: form.base_url || undefined,
        access_token: form.access_token || undefined,
        test_ip: testIp,
      });
      setTestResult(
        result.ok
          ? {
              ok: true,
              message: result.mac_address
                ? `Connected — ${testIp} → ${result.mac_address}`
                : (result.error ?? `Connected, but no active lease for ${testIp}.`),
            }
          : { ok: false, message: result.error ?? "Connection failed." },
      );
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof ApiError ? err.message : "Connection test failed" });
    } finally {
      setTesting(false);
    }
  }

  if (state.phase === "loading" || currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading settings…" />;
  }
  if (state.phase === "error") {
    return <ErrorState>{state.message}</ErrorState>;
  }

  const { settings } = state;
  const readyToTest = Boolean((form.access_token || settings.has_access_token) && form.base_url && testIp);

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">ClassGuard Integration</h1>
        <p className="mt-1 text-sm text-zinc-500">
          This org&apos;s own DHCP/DNS/web-filter platform. PrintOps uses its DHCP lease table to
          resolve a print job&apos;s source IP to a MAC address, which is then matched against
          Mosyle&apos;s cached devices for real user attribution (see the Jobs page).
        </p>
      </div>

      <Card>
        <CardTitle className="mb-4">Connection</CardTitle>
        <div className="flex flex-col gap-4">
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
                Off = Mosyle attribution never attempts a MAC lookup, even if credentials are
                configured.
              </span>
            </span>
          </label>
        </div>

        {actionError && <ErrorState>{actionError}</ErrorState>}
        <Button onClick={handleSave} disabled={saving} className="mt-4">
          {saving ? "Saving…" : "Save"}
        </Button>
      </Card>

      <Card>
        <CardTitle className="mb-1">Test Connection</CardTitle>
        <p className="mb-4 text-xs text-zinc-500">
          ClassGuard&apos;s only contract here is &quot;look up this IP&quot; — there&apos;s no
          separate health check, so testing means looking up a real IP currently on the network
          (e.g. your own computer&apos;s).
        </p>

        <div className="flex flex-col gap-4">
          <Field label="Test IP">
            <Input value={testIp} onChange={(e) => setTestIp(e.target.value)} placeholder="10.20.1.67" />
          </Field>

          {testResult && (
            <p
              className={`text-sm ${testResult.ok ? "text-emerald-700 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}
            >
              {testResult.ok ? "✓ " : "✗ "}
              {testResult.message}
            </p>
          )}

          <Button variant="secondary" onClick={handleTest} disabled={testing || !readyToTest}>
            {testing ? "Testing…" : "Test Connection"}
          </Button>
        </div>
      </Card>

      <Card>
        <CardTitle className="mb-4">Status</CardTitle>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm text-zinc-600 dark:text-zinc-400">
          <dt className="font-medium text-zinc-500">Status</dt>
          <dd>
            {settings.enabled ? <Badge tone="success">Enabled</Badge> : <Badge tone="neutral">Disabled</Badge>}
          </dd>
          <dt className="font-medium text-zinc-500">Last Tested</dt>
          <dd>{settings.last_test_at ? new Date(settings.last_test_at).toLocaleString() : "Never"}</dd>
        </dl>
        {settings.last_test_error && (
          <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
            <p className="font-medium">Last test failed.</p>
            <p className="mt-1 text-amber-800 dark:text-amber-300">{settings.last_test_error}</p>
          </div>
        )}
      </Card>
    </div>
  );
}
