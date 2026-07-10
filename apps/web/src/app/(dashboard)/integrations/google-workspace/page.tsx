"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  downloadCopierPinRoster,
  getGoogleWorkspaceSettings,
  syncGoogleWorkspaceDevices,
  testGoogleWorkspaceConnection,
  updateGoogleWorkspaceSettings,
  type CopierIdentityType,
  type GoogleWorkspaceSettings,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
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
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [form, setForm] = useState({
    service_account_json: "",
    admin_email: "",
    customer_id: "",
    staff_org_unit_path: "",
  });
  const [enabled, setEnabled] = useState(false);
  const [autoCopierIdentity, setAutoCopierIdentity] = useState(false);
  const [autoCopierIdentityType, setAutoCopierIdentityType] = useState<CopierIdentityType>("staff_id");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [exportingRoster, setExportingRoster] = useState(false);
  const DELEGATION_SCOPE = "https://www.googleapis.com/auth/admin.directory.device.chromeos.readonly";

  function handleCopyScope() {
    navigator.clipboard.writeText(DELEGATION_SCOPE).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  useEffect(() => {
    getGoogleWorkspaceSettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setForm((prev) => ({
          ...prev,
          admin_email: settings.admin_email ?? "",
          customer_id: settings.customer_id,
          staff_org_unit_path: settings.staff_org_unit_path ?? "",
        }));
        setEnabled(settings.enabled);
        setAutoCopierIdentity(settings.auto_create_copier_identity_from_employee_id);
        setAutoCopierIdentityType(settings.auto_copier_identity_type);
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

  function update(
    field: "service_account_json" | "admin_email" | "customer_id" | "staff_org_unit_path",
    value: string,
  ) {
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
        // Always sent (even blank) so clearing the field actually clears
        // the saved setting — see app/routers/settings.py's "" -> None rule.
        staff_org_unit_path: form.staff_org_unit_path,
        auto_create_copier_identity_from_employee_id: autoCopierIdentity,
        auto_copier_identity_type: autoCopierIdentityType,
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

  async function handleExportRoster() {
    setExportingRoster(true);
    setActionError(null);
    try {
      await downloadCopierPinRoster();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Export failed");
    } finally {
      setExportingRoster(false);
    }
  }

  if (state.phase === "loading" || currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading settings…" />;
  }
  if (state.phase === "error") {
    return <ErrorState>{state.message}</ErrorState>;
  }

  const { settings } = state;
  const readyToTest = Boolean((form.service_account_json || settings.has_service_account_json) && form.admin_email);

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Google Workspace Integration</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Resolves ChromeOS device→user attribution for print jobs (strategy 3, tried after Mosyle
          — see the Jobs page). Auth is a Google service account with domain-wide delegation, not a
          simple token.
        </p>
      </div>

      <Card>
        <details className="group" open={!settings.has_service_account_json}>
          <summary className="cursor-pointer list-none">
            <div className="flex items-center justify-between">
              <CardTitle className="mb-0">Setup Guide — Start Here</CardTitle>
              <span className="text-xs text-zinc-500 group-open:hidden">Show</span>
              <span className="hidden text-xs text-zinc-500 group-open:inline">Hide</span>
            </div>
          </summary>
          <div className="mt-4 flex flex-col gap-5 text-sm text-zinc-600 dark:text-zinc-400">
            <p>
              This uses two different Google sites, which is the part people most often get
              confused by: <strong className="font-medium">Google Cloud Console</strong> (for
              developers, where you create the credential) and{" "}
              <strong className="font-medium">Google Admin Console</strong> (your Workspace admin
              panel, where you authorize what that credential is allowed to read). You&apos;ll need
              super-admin access to the Admin Console step. It takes about five minutes.
            </p>

            <div>
              <p className="font-medium text-zinc-700 dark:text-zinc-300">
                Step 1 — Turn on the Admin SDK API
              </p>
              <p className="mt-1">
                Go to{" "}
                <a
                  href="https://console.cloud.google.com/"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent hover:underline"
                >
                  console.cloud.google.com
                </a>
                , select or create a project, then go to{" "}
                <strong className="font-medium">APIs &amp; Services → Library</strong>, search for{" "}
                <strong className="font-medium">Admin SDK API</strong>, open it, and click{" "}
                <strong className="font-medium">Enable</strong>.
              </p>
            </div>

            <div>
              <p className="font-medium text-zinc-700 dark:text-zinc-300">
                Step 2 — Create a service account and download its key
              </p>
              <p className="mt-1">
                Go to <strong className="font-medium">APIs &amp; Services → Credentials → Create
                Credentials → Service account</strong>. Give it any name (e.g.
                &quot;printops-workspace-sync&quot;) and click through the remaining steps using
                the defaults — you don&apos;t need to grant it any project roles.
              </p>
              <p className="mt-2">
                Once created, click on it, open the{" "}
                <strong className="font-medium">Keys</strong> tab, then{" "}
                <strong className="font-medium">Add Key → Create new key → JSON → Create</strong>.
                A <code className="text-[11px]">.json</code> file downloads — open it in any text
                editor, copy everything inside, and paste it into the{" "}
                <strong className="font-medium">Service Account JSON Key</strong> field below.
              </p>
            </div>

            <div>
              <p className="font-medium text-zinc-700 dark:text-zinc-300">
                Step 3 — Authorize it in Google Admin Console
              </p>
              <p className="mt-1">
                Still on that service account&apos;s page in Cloud Console, find and copy its{" "}
                <strong className="font-medium">Unique ID</strong> (a long number — different from
                its email address).
              </p>
              <p className="mt-2">
                Then, in a new tab, go to{" "}
                <a
                  href="https://admin.google.com/"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent hover:underline"
                >
                  admin.google.com
                </a>{" "}
                (not Cloud Console — this is a different site) and sign in with a super-admin
                account. Go to{" "}
                <strong className="font-medium">
                  Security → Access and data control → API controls → Domain-wide Delegation
                </strong>
                , click <strong className="font-medium">Add new</strong>, and paste in:
              </p>
              <ul className="mt-2 flex flex-col gap-3">
                <li>
                  <span className="font-medium text-zinc-700 dark:text-zinc-300">Client ID</span> —
                  the Unique ID you copied above.
                </li>
                <li>
                  <span className="font-medium text-zinc-700 dark:text-zinc-300">OAuth Scopes</span>{" "}
                  — paste in exactly (click to copy, so there&apos;s no typo):
                  <div className="mt-2 flex items-center gap-2">
                    <code className="flex-1 overflow-x-auto rounded-lg bg-zinc-100 px-3 py-2 text-[12px] text-zinc-800 dark:bg-white/[.08] dark:text-zinc-200">
                      {DELEGATION_SCOPE}
                    </code>
                    <Button type="button" variant="secondary" onClick={handleCopyScope}>
                      {copied ? "Copied!" : "Copy"}
                    </Button>
                  </div>
                </li>
              </ul>
              <p className="mt-2">
                Click <strong className="font-medium">Authorize</strong>.
              </p>
            </div>

            <div>
              <p className="font-medium text-zinc-700 dark:text-zinc-300">
                Step 4 — Fill in who it acts as
              </p>
              <p className="mt-1">
                A service account with domain-wide delegation must impersonate a real person to
                read the directory — enter that person&apos;s email in{" "}
                <strong className="font-medium">Admin Email</strong> below (any Workspace user with
                directory-read access; doesn&apos;t need to be a super-admin). Leave{" "}
                <strong className="font-medium">Customer ID</strong> as{" "}
                <code className="text-[11px]">my_customer</code> unless Google support has told you
                otherwise.
              </p>
            </div>

            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
              <p className="font-medium">Test Connection fails with a permission/unauthorized error?</p>
              <p className="mt-1">
                Most often this means the Client ID or scope in Domain-wide Delegation
                (admin.google.com) doesn&apos;t exactly match — re-check Step 3. Delegation changes
                can also take a few minutes to take effect after saving.
              </p>
            </div>
          </div>
        </details>
      </Card>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            1
          </span>
          <CardTitle className="mb-0">Service Account</CardTitle>
        </div>
        <p className="mb-4 ml-7 text-xs text-zinc-500">
          From the setup guide above: paste the full contents of the service account&apos;s JSON
          key file.
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
          permission. This is who the API calls are made &quot;as,&quot; not a secret itself.
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

      <Card>
        <div className="mb-1 flex items-center justify-between">
          <CardTitle>Copier PIN Roster</CardTitle>
          <Button variant="secondary" onClick={handleExportRoster} disabled={exportingRoster}>
            {exportingRoster ? "Exporting…" : "Export CSV"}
          </Button>
        </div>
        <p className="mb-4 text-xs text-zinc-500">
          One row per synced staff member with a Google Workspace Employee ID set (Name, Email,
          PIN) — for loading into a copier&apos;s local PIN/Account Track user list, e.g. Konica
          Minolta&apos;s User Authentication → User Registration screen. This is a starting column
          layout, not a confirmed match for any specific device&apos;s bulk-import format — check
          it against your copier&apos;s own admin panel and let us know if the columns need to
          change. Anyone without an Employee ID set in Workspace is skipped rather than given a
          made-up PIN.
        </p>
        <Field label="Staff Organizational Unit">
          <Input
            value={form.staff_org_unit_path}
            onChange={(e) => update("staff_org_unit_path", e.target.value)}
            placeholder="/Employees"
          />
          <span className="text-xs text-zinc-500">
            The exact OU path from admin.google.com → Directory → Organizational units (e.g.{" "}
            <code className="text-[11px]">/Employees</code>) that staff accounts live under —
            every district names this differently, so it&apos;s never assumed. Anyone in this OU
            or a nested one is included; everyone else is left out even if they have an Employee
            ID (e.g. students). Leave blank to include everyone with an Employee ID set.
          </span>
        </Field>

        <label className="mt-4 flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
          <input
            type="checkbox"
            className="mt-1"
            checked={autoCopierIdentity}
            onChange={(e) => setAutoCopierIdentity(e.target.checked)}
          />
          <span>
            Automatically use Employee ID as a copier login
            <br />
            <span className="text-xs text-zinc-500">
              Off by default — not every district wants this. When on, every synced staff
              member&apos;s Employee ID is added to Staff Copier Identities as a{" "}
              {autoCopierIdentity && (
                <select
                  value={autoCopierIdentityType}
                  onChange={(e) => setAutoCopierIdentityType(e.target.value as CopierIdentityType)}
                  className="mx-1 rounded border border-black/[.15] bg-transparent px-1 py-0.5 text-xs dark:border-white/[.2]"
                >
                  <option value="staff_id">Staff ID</option>
                  <option value="pin">PIN</option>
                  <option value="user_code">Vendor User Code</option>
                  <option value="department_id">Department ID</option>
                </select>
              )}
              {!autoCopierIdentity && "staff ID"}, kept in sync automatically — no manual entry
              needed. An admin&apos;s own manual entry for the same value always wins if one
              already exists.
            </span>
          </span>
        </label>

        <Button onClick={handleSave} disabled={saving} className="mt-3">
          {saving ? "Saving…" : "Save"}
        </Button>
      </Card>
    </div>
  );
}
