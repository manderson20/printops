"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ApiError,
  checkMfpDeviceCapabilities,
  checkMfpDeviceMeter,
  deleteMfpDevice,
  getMfpDevice,
  listConnectorTypes,
  listMfpDeviceUsage,
  testMfpDeviceConnection,
  updateMfpDevice,
  type ConnectorTypeOption,
  type CopierUsageRecord,
  type DeviceCapabilities,
  type MfpDevice,
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

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
  }, [params.id]);

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      const device = await updateMfpDevice(params.id, { ...form, capabilities: capabilities ?? undefined });
      setState({ phase: "ok", device });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to save changes");
    } finally {
      setSaving(false);
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
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">{device.name}</h1>
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
