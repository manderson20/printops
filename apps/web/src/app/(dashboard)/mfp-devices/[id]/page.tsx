"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ApiError,
  attributeMfpDeviceCopies,
  checkMfpDeviceCapabilities,
  checkMfpDeviceMeter,
  deleteMfpDevice,
  getMfpDevice,
  listConnectorTypes,
  listGoogleWorkspaceOrgUnits,
  listGoogleWorkspaceUsers,
  getLatestMfpSyncJob,
  listMfpDeviceAccounts,
  listMfpDeviceUsage,
  listMfpProvisionedAccounts,
  previewMfpDeviceProvisioning,
  pollMfpDeviceCounters,
  syncMfpDeviceUsers,
  testMfpDeviceConnection,
  updateMfpDevice,
  type ConnectorTypeOption,
  type CopierUsageRecord,
  type CounterPollResult,
  type DefaultOwnerAttributionResult,
  type DeviceCapabilities,
  type MfpDevice,
  type DeviceUser,
  type ProvisionedAccount,
  type ProvisioningPreview,
  type GoogleWorkspaceUserEntry,
  type SyncJob,
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { OrgUnitPicker } from "@/components/ui/OrgUnitPicker";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; device: MfpDevice }
  | { phase: "error"; message: string };

const EDITABLE_FIELDS = [
  ["name", "Name"],
  ["model", "Model"],
  ["serial_number", "Serial Number"],
  ["ip_address", "IP Address"],
  ["hostname", "Hostname"],
  ["building", "Building"],
  ["room", "Room"],
  ["department", "Department"],
  ["notes", "Notes"],
] as const;

const CAPABILITY_LABELS: [keyof DeviceCapabilities, string][] = [
  ["walkup_copy_accounting", "Walk-up copy accounting"],
  ["user_code_pin_auth", "User code / PIN auth"],
  ["badge_card_auth", "Badge / card auth"],
  ["department_id_accounting", "Department ID accounting"],
  ["ldap_auth", "LDAP auth"],
  ["local_user_table", "Local user table"],
  ["remote_user_provisioning", "Remote user provisioning"],
  ["csv_accounting_export", "CSV accounting export"],
  ["api_accounting_retrieval", "API accounting retrieval"],
  ["snmp_meter_counters", "SNMP meter counters"],
  ["scan_accounting", "Scan accounting"],
  ["color_mono_accounting", "Color/mono accounting"],
  ["quotas", "Quotas / limits"],
  ["secure_print_release", "Secure print release (future)"],
];

function capabilityTone(value: boolean | null): "success" | "danger" | "neutral" {
  if (value === true) return "success";
  if (value === false) return "danger";
  return "neutral";
}

export default function MfpDeviceDetailPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const isAdmin = useCurrentUser()?.role === "admin";
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [form, setForm] = useState<Record<string, string>>({});
  const [capabilities, setCapabilities] = useState<DeviceCapabilities | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [checkingCaps, setCheckingCaps] = useState(false);
  const [checkingMeter, setCheckingMeter] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [usage, setUsage] = useState<CopierUsageRecord[] | null>(null);
  const [connectorTypes, setConnectorTypes] = useState<ConnectorTypeOption[]>([]);
  // Copy-tracking settings, kept out of `form` because they aren't plain
  // text fields: the OU list is a multi-select of real synced OUs, and the
  // admin password is write-only (never sent back by the API, so it starts
  // blank and is only submitted when actually retyped).
  const [provisionOus, setProvisionOus] = useState<string[]>([]);
  const [adminUsername, setAdminUsername] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [autoSyncUsers, setAutoSyncUsers] = useState(false);
  const [preview, setPreview] = useState<ProvisioningPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [deviceAccounts, setDeviceAccounts] = useState<DeviceUser[] | null>(null);
  const [loadingAccounts, setLoadingAccounts] = useState(false);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [job, setJob] = useState<SyncJob | null>(null);
  const [autoPollCounters, setAutoPollCounters] = useState(false);
  const [pollingCounters, setPollingCounters] = useState(false);
  const [pollResult, setPollResult] = useState<CounterPollResult | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [owners, setOwners] = useState<ProvisionedAccount[] | null>(null);
  // Whole-device attribution: one named person owns every copy this
  // machine makes, for a desk copier nobody logs in to.
  const [defaultOwnerEmail, setDefaultOwnerEmail] = useState("");
  const [attributing, setAttributing] = useState(false);
  const [attribution, setAttribution] = useState<DefaultOwnerAttributionResult | null>(null);
  const [staffOptions, setStaffOptions] = useState<GoogleWorkspaceUserEntry[]>([]);

  useEffect(() => {
    getMfpDevice(params.id)
      .then((device) => {
        setState({ phase: "ok", device });
        setForm(
          Object.fromEntries(
            EDITABLE_FIELDS.map(([field]) => [field, (device as never)[field] ?? ""]),
          ),
        );
        setCapabilities(device.capabilities);
        setProvisionOus(device.provision_org_unit_paths ?? []);
        setAdminUsername(device.admin_username ?? "");
        setAutoSyncUsers(device.auto_sync_users);
        setAutoPollCounters(device.auto_poll_counters);
        setDefaultOwnerEmail(device.default_owner_email ?? "");
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load MFP device",
        }),
      );
    listMfpDeviceUsage(params.id, 20)
      .then(setUsage)
      .catch(() => setUsage([]));
    listConnectorTypes()
      .then(setConnectorTypes)
      .catch(() => setConnectorTypes([]));
    // Load the preview up front. Requiring a click before the Sync button
    // becomes usable made it look broken.
    previewMfpDeviceProvisioning(params.id)
      .then(setPreview)
      .catch(() => setPreview(null));
    getLatestMfpSyncJob(params.id)
      .then(setJob)
      .catch(() => setJob(null));
    listMfpProvisionedAccounts(params.id)
      .then(setOwners)
      .catch(() => setOwners(null));
    // Roster for the owner picker. Best-effort: the field stays a plain
    // email input if Workspace isn't synced, rather than blocking on it.
    listGoogleWorkspaceUsers()
      .then(setStaffOptions)
      .catch(() => setStaffOptions([]));
  }, [params.id]);

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      const device = await updateMfpDevice(params.id, {
        ...form,
        capabilities: capabilities ?? undefined,
        // null (not []) means "no per-copier narrowing" — an empty list
        // would read as "provision nobody".
        provision_org_unit_paths: provisionOus.length > 0 ? provisionOus : null,
        admin_username: adminUsername || null,
        auto_sync_users: autoSyncUsers,
        auto_poll_counters: autoPollCounters,
        // Empty string clears it; the API maps that to null.
        default_owner_email: defaultOwnerEmail.trim(),
        // Only sent when retyped; omitting it leaves the stored password
        // alone, same as the SNMP community field elsewhere.
        ...(adminPassword ? { admin_password: adminPassword } : {}),
      });
      setAdminPassword("");
      setState({ phase: "ok", device });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to save changes");
    } finally {
      setSaving(false);
    }
  }

  // Poll while a sync is in flight. A sync is minutes long, so without
  // this the page can only show a spinner and an admin cannot tell a slow
  // run from a stuck one.
  useEffect(() => {
    if (!job || (job.status !== "running" && job.status !== "pending")) return;
    const timer = setInterval(() => {
      getLatestMfpSyncJob(params.id)
        .then((latest) => {
          setJob(latest);
          if (latest && latest.status !== "running" && latest.status !== "pending") {
            listMfpProvisionedAccounts(params.id).then(setOwners).catch(() => {});
            getMfpDevice(params.id)
              .then((d) => setState({ phase: "ok", device: d }))
              .catch(() => {});
          }
        })
        .catch(() => {});
    }, 2000);
    return () => clearInterval(timer);
  }, [job, params.id]);

  async function handleLoadDeviceAccounts() {
    setLoadingAccounts(true);
    setAccountsError(null);
    try {
      setDeviceAccounts(await listMfpDeviceAccounts(params.id));
    } catch (err) {
      setDeviceAccounts(null);
      setAccountsError(
        err instanceof ApiError ? err.message : "Could not read the copier's accounts",
      );
    } finally {
      setLoadingAccounts(false);
    }
  }

  async function handlePreviewUsers() {
    setPreviewing(true);
    setActionError(null);
    try {
      setPreview(await previewMfpDeviceProvisioning(params.id));
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Could not work out who would sync");
    } finally {
      setPreviewing(false);
    }
  }

  async function handleSyncUsers(rewrite = false) {
    const question = rewrite
      ? `Rewrite all staff accounts on ${state.phase === "ok" ? state.device.name : "this copier"}?\n\n` +
        "Every in-scope person is re-seated into a known account slot and their code is " +
        "re-set, so PrintOps knows exactly whose account is whose. Existing codes on the " +
        "copier will change."
      : `Add ${preview?.count ?? "these"} staff accounts to ${state.phase === "ok" ? state.device.name : "this copier"}?\n\n` +
        "Anyone already set up is skipped. Nothing is deleted.";
    if (!confirm(question)) return;
    setSyncing(true);
    setActionError(null);
    try {
      setJob(await syncMfpDeviceUsers(params.id, { rewrite }));
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Could not start the sync");
    } finally {
      setSyncing(false);
    }
  }

  async function handlePollCounters() {
    setPollingCounters(true);
    setPollError(null);
    try {
      setPollResult(await pollMfpDeviceCounters(params.id));
      // The counters just produced usage rows, and the device row now
      // carries when it last worked — both are on this page.
      listMfpDeviceUsage(params.id, 20).then(setUsage).catch(() => {});
      getMfpDevice(params.id)
        .then((d) => setState({ phase: "ok", device: d }))
        .catch(() => {});
    } catch (err) {
      setPollError(err instanceof ApiError ? err.message : "Failed to read the copier's counters");
    } finally {
      setPollingCounters(false);
    }
  }

  async function handleAttributeCopies() {
    setAttributing(true);
    setActionError(null);
    try {
      setAttribution(await attributeMfpDeviceCopies(params.id));
      // The run may have written usage rows and moved the watermark, both
      // of which are shown on this page.
      listMfpDeviceUsage(params.id, 20).then(setUsage).catch(() => {});
      getMfpDevice(params.id)
        .then((d) => setState({ phase: "ok", device: d }))
        .catch(() => {});
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "Failed to attribute this copier's copies",
      );
    } finally {
      setAttributing(false);
    }
  }

  async function handleTestConnection() {
    setTesting(true);
    setActionError(null);
    try {
      const device = await testMfpDeviceConnection(params.id);
      setState({ phase: "ok", device });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Connection test failed");
    } finally {
      setTesting(false);
    }
  }

  async function handleCheckCapabilities() {
    setCheckingCaps(true);
    setActionError(null);
    try {
      const device = await checkMfpDeviceCapabilities(params.id);
      setState({ phase: "ok", device });
      setCapabilities(device.capabilities);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Capability check failed");
    } finally {
      setCheckingCaps(false);
    }
  }

  async function handleCheckMeter() {
    setCheckingMeter(true);
    setActionError(null);
    try {
      const device = await checkMfpDeviceMeter(params.id);
      setState({ phase: "ok", device });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Meter check failed");
    } finally {
      setCheckingMeter(false);
    }
  }

  async function handleDelete() {
    if (!confirm("Delete this MFP device? Its usage history stays, but will lose the device link.")) return;
    await deleteMfpDevice(params.id);
    router.push("/mfp-devices");
  }

  if (state.phase === "loading") {
    return <Spinner label="Loading MFP device…" />;
  }
  if (state.phase === "error") {
    return <ErrorState>{state.message}</ErrorState>;
  }

  const { device } = state;

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">{device.name}</h1>
          <WikiHelpLink page="Copier-Accounting" anchor="copiers" />
        </div>
        {isAdmin && (
          <Button variant="danger" onClick={handleDelete}>
            Delete
          </Button>
        )}
      </div>

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <CardTitle>Connection</CardTitle>
          {isAdmin && (
            <Button variant="secondary" onClick={handleTestConnection} disabled={testing}>
              {testing ? "Testing…" : "Test Connection"}
            </Button>
          )}
        </div>
        <div className="flex flex-col gap-2 text-sm">
          <div className="flex items-center gap-2">
            <Badge tone="info">{device.connector_type}</Badge>
            <Badge tone="neutral">{device.vendor}</Badge>
          </div>
          {device.last_test_connection_at ? (
            <p className="text-zinc-600 dark:text-zinc-400">
              Last tested {formatRelativeTime(device.last_test_connection_at)} —{" "}
              <span className={device.last_test_connection_ok ? "text-emerald-700 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}>
                {device.last_test_connection_ok ? "OK" : "Failed"}
              </span>
              {device.last_test_connection_message && `: ${device.last_test_connection_message}`}
            </p>
          ) : (
            <p className="text-zinc-500">Never tested.</p>
          )}
          {(() => {
            const notes = connectorTypes.find((c) => c.connector_type === device.connector_type)
              ?.setup_notes;
            return notes ? (
              <div className="mt-1 rounded border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200">
                <p className="font-medium">Device setup</p>
                <p className="mt-1">{notes}</p>
              </div>
            ) : null;
          })()}
        </div>
      </Card>

      <Card>
        <CardTitle className="mb-4">Details</CardTitle>
        <div className="grid grid-cols-2 gap-4">
          {EDITABLE_FIELDS.map(([field, label]) => (
            <Field key={field} label={label}>
              <Input
                value={form[field] ?? ""}
                disabled={!isAdmin}
                onChange={(e) => setForm((prev) => ({ ...prev, [field]: e.target.value }))}
              />
            </Field>
          ))}
        </div>
        {actionError && <ErrorState>{actionError}</ErrorState>}
        {isAdmin && (
          <Button onClick={handleSave} disabled={saving} className="mt-4">
            {saving ? "Saving…" : "Save"}
          </Button>
        )}
      </Card>

      <Card>
        <CardTitle className="mb-1">Copy Tracking</CardTitle>
        <p className="mb-4 text-xs text-zinc-500">
          Who this copier tracks, and how PrintOps signs in to it. Staff still have to exist as
          accounts on the copier itself before they can log in at the panel — PrintOps knowing
          someone&apos;s ID doesn&apos;t make the copier accept it.
        </p>

        <Field label="Staff Organizational Units">
          <OrgUnitPicker
            value={provisionOus}
            onChange={setProvisionOus}
            load={listGoogleWorkspaceOrgUnits}
            disabled={!isAdmin}
            addLabel="Add an organizational unit…"
            emptyLabel="Everyone covered by the org-wide setting."
          />
          <span className="text-xs text-zinc-500">
            The staff who get accounts on <em>this</em> copier. Leave empty
            to include everyone covered by the org-wide setting under Settings → Integrations →
            Google Workspace. Worth narrowing per building: a copier holds a limited number of
            accounts (a Lexmark XM3350 takes 250, a Konica bizhub 1,000), and the people who walk
            up to a building&apos;s copier are that building&apos;s staff.
          </span>
        </Field>

        <div className="mt-4 grid grid-cols-2 gap-4">
          <Field label="Device Admin Username">
            <Input
              value={adminUsername}
              disabled={!isAdmin}
              onChange={(e) => setAdminUsername(e.target.value)}
              placeholder="(leave blank if none)"
              autoComplete="off"
              name="printops-device-admin-username"
            />
            <span className="text-xs text-zinc-500">
              Konica bizhub and Lexmark XM3350 admin logins ask for a password only — leave this
              blank for those.
            </span>
          </Field>
          <Field label="Device Admin Password">
            {/* A bare password input invites the browser to autofill a
                saved password and then save it over a working device
                credential — which is exactly what happened once. Konica's
                own admin form sets autocomplete="off" here for the same
                reason. */}
            <Input
              type="password"
              value={adminPassword}
              disabled={!isAdmin}
              onChange={(e) => setAdminPassword(e.target.value)}
              autoComplete="new-password"
              name="printops-device-admin-password"
              data-lpignore="true"
              data-1p-ignore="true"
              placeholder={
                state.device.has_admin_password ? "•••••••• (saved)" : "(none saved)"
              }
            />
            <span className="text-xs text-zinc-500">
              Stored encrypted and never shown again. Used by the connector to read per-user
              counts and register accounts. Leave blank to keep the saved one.
            </span>
          </Field>
        </div>

        <label className="mt-4 flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
          <input
            type="checkbox"
            className="mt-1"
            disabled={!isAdmin}
            checked={autoSyncUsers}
            onChange={(e) => setAutoSyncUsers(e.target.checked)}
          />
          <span>
            Keep this copier&apos;s accounts up to date automatically
            <br />
            <span className="text-xs text-zinc-500">
              Checks every six hours and adds any in-scope staff who don&apos;t have an account
              yet. Off by default — turn it on once a manual sync has done what you expect.
              Nothing is ever deleted from the copier automatically.
            </span>
          </span>
        </label>

        {isAdmin && (
          <Button onClick={handleSave} disabled={saving} className="mt-4">
            {saving ? "Saving…" : "Save"}
          </Button>
        )}
      </Card>

      {isAdmin && (
        <Card>
          <div className="mb-1 flex items-center justify-between">
            <CardTitle>Staff Accounts On This Copier</CardTitle>
            <div className="flex gap-2">
              <Button variant="secondary" onClick={handlePreviewUsers} disabled={previewing}>
                {previewing ? "Checking…" : "Preview"}
              </Button>
              <Button
                onClick={() => handleSyncUsers(false)}
                disabled={syncing || job?.status === "running" || job?.status === "pending"}
              >
                {job?.status === "running" || job?.status === "pending"
                  ? "Syncing…"
                  : "Sync Users to Copier"}
              </Button>
            </div>
          </div>
          <p className="mb-3 text-xs text-zinc-500">
            Adds each in-scope staff member as an account on the copier, with their 5-digit
            Employee ID as the code they type at the panel. Accounts already on the copier are
            left alone, and nothing is ever deleted. Preview first — it shows exactly who would
            be added without touching the copier.
          </p>

          {preview && (
            <div className="mb-3 rounded border border-black/[.08] p-3 text-sm dark:border-white/[.12]">
              <div>
                <strong>{preview.count}</strong> staff in scope
                {preview.org_unit_paths.length > 0 && (
                  <>
                    {" "}
                    from <code className="text-[11px]">{preview.org_unit_paths.join(", ")}</code>
                  </>
                )}
                {preview.excluded_org_unit_paths.length > 0 && (
                  <>
                    , excluding{" "}
                    <code className="text-[11px]">
                      {preview.excluded_org_unit_paths.join(", ")}
                    </code>
                  </>
                )}
                .
              </div>
              {preview.sample_emails.length > 0 && (
                <div className="mt-1 text-xs text-zinc-500">
                  For example: {preview.sample_emails.slice(0, 5).join(", ")}
                  {preview.count > 5 && ` and ${preview.count - 5} more`}
                </div>
              )}
              {preview.skipped_no_org_unit > 0 && (
                <div className="mt-2 text-xs text-amber-700 dark:text-amber-500">
                  {preview.skipped_no_org_unit} staff copier{" "}
                  {preview.skipped_no_org_unit === 1 ? "identity is" : "identities are"} not in the
                  synced Google Workspace directory, so we can&apos;t tell which organizational
                  unit they belong to. They are left out. This usually means the Workspace sync
                  needs to run.
                </div>
              )}
            </div>
          )}

          {job && (
            <div className="mb-3 rounded border border-black/[.08] p-3 dark:border-white/[.12]">
              {(job.status === "running" || job.status === "pending") && job.total > 0 ? (
                <>
                  <div className="mb-1 flex items-center justify-between text-sm">
                    <span>Adding accounts to the copier…</span>
                    <span className="font-mono text-xs">
                      {job.completed + job.failed} of {job.total}
                    </span>
                  </div>
                  <div
                    className="h-2 w-full overflow-hidden rounded bg-black/[.08] dark:bg-white/[.15]"
                    role="progressbar"
                    aria-valuenow={job.completed + job.failed}
                    aria-valuemin={0}
                    aria-valuemax={job.total}
                  >
                    <div
                      className="h-full bg-emerald-600 transition-all"
                      style={{
                        width: `${Math.round(((job.completed + job.failed) / job.total) * 100)}%`,
                      }}
                    />
                  </div>
                  <p className="mt-1 text-xs text-zinc-500">
                    This takes a few minutes — the copier accepts one account at a time. You can
                    leave this page; it keeps running.
                  </p>
                </>
              ) : job.status === "pending" || job.status === "running" ? (
                <p className="text-sm">Starting…</p>
              ) : (
                <p className="text-sm">
                  <strong>
                    {job.status === "succeeded" ? "Sync finished" : "Sync finished with problems"}
                  </strong>
                  {" — "}
                  {job.completed} added
                  {job.failed > 0 && `, ${job.failed} failed`}
                  {job.skipped > 0 && `, ${job.skipped} already set up`}.
                  {job.message && (
                    <span className="mt-1 block text-xs text-zinc-500">{job.message}</span>
                  )}
                </p>
              )}
            </div>
          )}

          {owners && owners.length > 0 && (
            <div className="mb-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
                  Who is who on this copier
                </span>
                <Button
                  variant="secondary"
                  onClick={() => handleSyncUsers(true)}
                  disabled={syncing || job?.status === "running" || job?.status === "pending"}
                >
                  Rewrite All
                </Button>
              </div>
              <p className="mb-1 text-xs text-zinc-500">
                {owners.length} accounts PrintOps has set up here. The copier itself can&apos;t
                tell you this — it never reveals an account&apos;s code — so this is the record of
                which slot belongs to whom.
              </p>
              <ul className="max-h-56 overflow-y-auto text-xs">
                {owners.map((o) => (
                  <li key={o.device_account_id} className="py-0.5">
                    <span className="font-mono">#{o.device_account_id}</span>{" "}
                    <span className="font-mono text-zinc-500">
                      {o.device_account_name ?? "—"}
                    </span>{" "}
                    — {o.staff_email}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mb-3 border-t border-black/[.06] pt-3 dark:border-white/[.1]">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
                Accounts currently on the copier
              </span>
              <Button
                variant="secondary"
                onClick={handleLoadDeviceAccounts}
                disabled={loadingAccounts}
              >
                {loadingAccounts ? "Reading…" : deviceAccounts ? "Refresh" : "Show"}
              </Button>
            </div>
            {accountsError && <ErrorState>{accountsError}</ErrorState>}
            {deviceAccounts !== null &&
              (deviceAccounts.length === 0 ? (
                <p className="text-xs text-zinc-500">
                  The copier has no accounts registered yet.
                </p>
              ) : (
                <>
                  <p className="mb-1 text-xs text-zinc-500">
                    {deviceAccounts.length} registered on the device. Read live from the copier,
                    not from PrintOps.
                  </p>
                  <ul className="max-h-48 overflow-y-auto text-xs">
                    {deviceAccounts.map((account) => (
                      <li key={account.identifier} className="py-0.5 font-mono">
                        #{account.identifier}
                        {account.name ? ` — ${account.name}` : ""}
                        {!account.has_password && " (no code set)"}
                        {account.disabled && " (disabled)"}
                      </li>
                    ))}
                  </ul>
                </>
              ))}
          </div>

          {device.last_user_sync_at && (
            <p className="text-xs text-zinc-500">
              Last sync {formatRelativeTime(device.last_user_sync_at)}
              {device.last_user_sync_ok === false && " — had problems"}
              {device.last_user_sync_message && `: ${device.last_user_sync_message}`}
            </p>
          )}
        </Card>
      )}

      {isAdmin && (
        <Card>
          <div className="mb-1 flex items-center justify-between">
            <CardTitle>Copy Counts From This Copier</CardTitle>
            <Button onClick={handlePollCounters} disabled={pollingCounters}>
              {pollingCounters ? "Reading…" : "Read Counts Now"}
            </Button>
          </div>
          <p className="mb-3 text-xs text-zinc-500">
            The copier counts pages per account as a running total that never resets, so it can
            only say how many pages an account has made <em>ever</em>. PrintOps records each
            reading and reports the difference from the one before it — which is why the first
            read produces no usage at all, and every one after it covers exactly the time since
            the last read.
          </p>

          {pollError && <ErrorState>{pollError}</ErrorState>}

          {pollResult && (
            <div className="mb-3 rounded border border-black/[.08] p-3 text-sm dark:border-white/[.12]">
              {pollResult.baselines > 0 && pollResult.usage_rows === 0 ? (
                <>
                  <strong>First reading taken</strong> — {pollResult.baselines} accounts. There
                  is nothing to compare against yet, so no usage was recorded. The next read is
                  the one that produces numbers.
                </>
              ) : pollResult.baseline_usage_rows > 0 ? (
                <>
                  <strong>{pollResult.usage_rows}</strong>{" "}
                  {pollResult.usage_rows === 1 ? "entry" : "entries"} recorded, of which{" "}
                  {pollResult.baseline_usage_rows} came from a first reading — pages made
                  between PrintOps setting the account up and this read.
                </>
              ) : (
                <>
                  <strong>{pollResult.usage_rows}</strong>{" "}
                  {pollResult.usage_rows === 1 ? "entry" : "entries"} recorded from{" "}
                  {pollResult.changed} {pollResult.changed === 1 ? "account" : "accounts"}, out of{" "}
                  {pollResult.accounts_read} read.
                </>
              )}
              {pollResult.unmapped > 0 && (
                <div className="mt-1 text-xs text-amber-700 dark:text-amber-500">
                  {pollResult.unmapped} of them{" "}
                  {pollResult.unmapped === 1 ? "is an account" : "are accounts"} PrintOps
                  didn&apos;t set up, so the pages aren&apos;t matched to anyone. They&apos;re
                  kept, and show up under Unmapped Activity.
                </div>
              )}
              {pollResult.resets > 0 && (
                <div className="mt-1 text-xs text-amber-700 dark:text-amber-500">
                  {pollResult.resets}{" "}
                  {pollResult.resets === 1 ? "account's counter was" : "accounts' counters were"}{" "}
                  cleared on the copier since the last read. The pages counted since that clear
                  were recorded.
                </div>
              )}
            </div>
          )}

          <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              className="mt-1"
              checked={autoPollCounters}
              onChange={(e) => setAutoPollCounters(e.target.checked)}
            />
            <span>
              Read the counts automatically
              <br />
              <span className="text-xs text-zinc-500">
                Every hour. This only reads — it never changes anything on the copier. The
                interval is also how precisely a copy can be dated, since the counters carry no
                timestamps: all PrintOps can say is that the pages happened between two reads.
              </span>
            </span>
          </label>

          <Button onClick={handleSave} disabled={saving} className="mt-4">
            {saving ? "Saving…" : "Save"}
          </Button>

          {device.last_counter_poll_at && (
            <p className="mt-3 text-xs text-zinc-500">
              Last read {formatRelativeTime(device.last_counter_poll_at)}
              {device.last_counter_poll_ok === false && " — failed"}
              {device.last_counter_poll_message && `: ${device.last_counter_poll_message}`}
            </p>
          )}
        </Card>
      )}

      {isAdmin && (
        <Card>
          <CardTitle className="mb-1">Whole-Device Copy Owner</CardTitle>
          <p className="mb-4 text-xs text-zinc-500">
            For a desk copier with one regular user and no per-user accounting —
            nobody logs in, so there is no code to match a person to. Naming an
            owner credits them with every copy the machine&apos;s own meter
            records from that moment on.
          </p>

          <Field label="Owner">
            <Input
              list="staff-owner-options"
              value={defaultOwnerEmail}
              placeholder="nobody — copies on this device belong to no one"
              onChange={(e) => setDefaultOwnerEmail(e.target.value)}
            />
          </Field>
          <datalist id="staff-owner-options">
            {staffOptions.map((person) => (
              <option key={person.email} value={person.email}>
                {person.name ?? person.email}
              </option>
            ))}
          </datalist>

          <p className="mt-2 text-xs text-zinc-500">
            Counting starts when you save, never from the meter&apos;s lifetime
            total — nobody is handed the copies made before they were named.
            Changing the owner restarts it for the same reason.
          </p>

          {/* The conditions the feature actually depends on, stated up
              front rather than discovered as a "nothing happened" later. */}
          {(autoPollCounters || device.auto_poll_counters) && (
            <p className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
              This copier reads per-account counters, which already say who
              made each copy. A whole-device owner would count those same
              copies a second time, so it won&apos;t be applied while
              per-account reading is on — turn that off first if this machine
              really does belong to one person.
            </p>
          )}
          {!device.printer_id && (
            <p className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
              This copier isn&apos;t linked to a printer, so PrintOps has no
              meter to read. Link it on the printer&apos;s page first.
            </p>
          )}
          {device.printer_id &&
            device.page_count_confidence !== null &&
            device.page_count_confidence !== "verified" &&
            device.page_count_confidence !== "best_effort" && (
              <p className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
                This device&apos;s meter reports one combined page total rather
                than a separate copy counter, so its copies can&apos;t be
                measured — only estimated, which the Untracked Copy Activity
                report already does org-wide.
              </p>
            )}

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button
              variant="secondary"
              onClick={handleAttributeCopies}
              disabled={attributing || !device.default_owner_email}
            >
              {attributing ? "Attributing…" : "Attribute copies now"}
            </Button>
          </div>

          {attribution && (
            <p
              className={`mt-3 text-xs ${
                attribution.skipped_reason
                  ? "text-amber-700 dark:text-amber-500"
                  : "text-zinc-500"
              }`}
            >
              {attribution.message}
            </p>
          )}

          {device.default_owner_email && device.default_owner_attributed_through && (
            <p className="mt-1 text-xs text-zinc-500">
              Copies counted through{" "}
              {formatRelativeTime(device.default_owner_attributed_through)}.
            </p>
          )}
          {device.default_owner_email && !device.default_owner_attributed_through && (
            <p className="mt-1 text-xs text-zinc-500">
              Nothing counted yet — the first run records where the meter stands
              now, and counts from there.
            </p>
          )}
        </Card>
      )}

      <Card>
        <div className="mb-1 flex items-center justify-between">
          <CardTitle>Capabilities</CardTitle>
          {isAdmin && (
            <Button variant="secondary" onClick={handleCheckCapabilities} disabled={checkingCaps}>
              {checkingCaps ? "Checking…" : "Check via Connector"}
            </Button>
          )}
        </div>
        <p className="mb-4 text-xs text-zinc-500">
          Not every copier supports every capability — &quot;Unknown&quot; means it hasn&apos;t
          been assessed yet, not that it&apos;s unsupported.
          {device.capabilities_detected_at &&
            ` Last assessed ${formatRelativeTime(device.capabilities_detected_at)} (${device.capabilities_source}).`}
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {CAPABILITY_LABELS.map(([key, label]) => {
            const value = capabilities?.[key] ?? null;
            return (
              <div key={key} className="flex items-center justify-between gap-2 text-sm">
                <span className="text-zinc-700 dark:text-zinc-300">{label}</span>
                {isAdmin ? (
                  <select
                    value={value === null ? "unknown" : String(value)}
                    onChange={(e) => {
                      const raw = e.target.value;
                      const newValue = raw === "unknown" ? null : raw === "true";
                      setCapabilities((prev) => (prev ? { ...prev, [key]: newValue } : prev));
                    }}
                    className="rounded border border-black/[.15] bg-transparent px-2 py-1 text-xs dark:border-white/[.2]"
                  >
                    <option value="unknown">Unknown</option>
                    <option value="true">Yes</option>
                    <option value="false">No</option>
                  </select>
                ) : (
                  <Badge tone={capabilityTone(value)}>
                    {value === null ? "Unknown" : value ? "Yes" : "No"}
                  </Badge>
                )}
              </div>
            );
          })}
        </div>
      </Card>

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <CardTitle>Meter</CardTitle>
          {isAdmin && (
            <Button variant="secondary" onClick={handleCheckMeter} disabled={checkingMeter}>
              {checkingMeter ? "Checking…" : "Check Meter"}
            </Button>
          )}
        </div>
        {device.page_count_error && !device.page_count_total ? (
          <p className="text-sm text-amber-700 dark:text-amber-400">{device.page_count_error}</p>
        ) : (
          <dl className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <dt className="text-xs font-medium text-zinc-500">Total</dt>
              <dd className="text-zinc-800 dark:text-zinc-200">{device.page_count_total ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-zinc-500">Copy</dt>
              <dd className="text-zinc-800 dark:text-zinc-200">{device.page_count_copy ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-zinc-500">Print</dt>
              <dd className="text-zinc-800 dark:text-zinc-200">{device.page_count_print ?? "—"}</dd>
            </div>
          </dl>
        )}
        {device.page_count_checked_at && (
          <p className="mt-2 text-xs text-zinc-400">
            Checked {formatRelativeTime(device.page_count_checked_at)}
            {device.page_count_confidence && ` · ${device.page_count_confidence}`}
          </p>
        )}
      </Card>

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <CardTitle>Recent Copier Usage</CardTitle>
          <Link
            href={`/copier-imports?device_id=${device.id}`}
            className="text-xs font-medium text-accent hover:underline"
          >
            Import accounting data
          </Link>
        </div>
        {usage === null && <Spinner label="Loading usage…" />}
        {usage !== null && usage.length === 0 && (
          <EmptyState>No copier usage recorded for this device yet.</EmptyState>
        )}
        {usage !== null && usage.length > 0 && (
          <div className="flex flex-col gap-2 text-sm">
            {usage.map((record) => (
              <div
                key={record.id}
                className="flex items-center justify-between border-t border-black/[.08] pt-2 first:border-t-0 first:pt-0 dark:border-white/[.1]"
              >
                <div className="flex flex-col">
                  <span className="text-zinc-700 dark:text-zinc-300">
                    {record.staff_email ?? (
                      <span className="text-amber-700 dark:text-amber-400">
                        Unmapped ({record.external_identity_used})
                      </span>
                    )}
                  </span>
                  <span className="text-xs text-zinc-400">
                    {record.occurred_at
                      ? new Date(record.occurred_at).toLocaleString()
                      : `${record.period_start ?? "?"} – ${record.period_end ?? "?"}`}
                  </span>
                </div>
                <span className="text-zinc-600 dark:text-zinc-400">{record.page_count ?? "—"} pages</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
