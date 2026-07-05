"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  createAttributionAlias,
  deleteAttributionAlias,
  deleteDeviceOverride,
  listAttributionAliases,
  listGoogleWorkspaceUsers,
  listKnownDevices,
  setDeviceOverride,
  type AttributionAlias,
  type GoogleWorkspaceUserEntry,
  type KnownDevice,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; devices: KnownDevice[] }
  | { phase: "error"; message: string };

const SOURCE_LABEL: Record<KnownDevice["source"], string> = {
  mosyle: "Mosyle",
  google_workspace: "Google Workspace",
};

// Shared across every row rather than one per row — the roster can be
// thousands of entries (this org's Google Workspace sync has 3500+), and
// with 2000+ devices a per-row copy meant rendering roster_size * device_count
// <option> elements (tens of millions of DOM nodes), which froze the tab
// solid. A single shared datalist still lets every row's input reference it.
const ROSTER_DATALIST_ID = "google-workspace-roster";

function DeviceRow({
  device,
  onSaved,
}: {
  device: KnownDevice;
  onSaved: () => void;
}) {
  const [email, setEmail] = useState(device.override_email ?? "");
  const [note, setNote] = useState(device.override_note ?? "");
  const [saving, setSaving] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  async function handleSave() {
    setSaving(true);
    setRowError(null);
    setLastResult(null);
    try {
      const result = await setDeviceOverride(device.mac_address, {
        resolved_email: email,
        note: note || null,
      });
      setLastResult(
        result.backfilled_job_count > 0
          ? `Saved — updated ${result.backfilled_job_count} past job${result.backfilled_job_count === 1 ? "" : "s"}.`
          : "Saved.",
      );
      onSaved();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to save override");
    } finally {
      setSaving(false);
    }
  }

  async function handleClear() {
    setSaving(true);
    setRowError(null);
    setLastResult(null);
    try {
      await deleteDeviceOverride(device.mac_address);
      setEmail("");
      setNote("");
      onSaved();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to clear override");
    } finally {
      setSaving(false);
    }
  }

  return (
    <tr className="border-b border-black/[.08] last:border-0 align-top dark:border-white/[.145]">
      <td className="px-4 py-3 font-mono text-xs text-zinc-600 dark:text-zinc-400">{device.mac_address}</td>
      <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">{SOURCE_LABEL[device.source]}</td>
      <td className="px-4 py-3 text-black dark:text-zinc-50">
        {device.device_name ?? "—"}
        {device.serial_number && (
          <div className="text-xs text-zinc-500">{device.serial_number}</div>
        )}
      </td>
      <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
        {device.reported_email ?? device.reported_username ?? "—"}
      </td>
      <td className="px-4 py-3">
        <div className="flex flex-col gap-2">
          <input
            list={ROSTER_DATALIST_ID}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="correct-user@domain.com"
            className="rounded border border-black/[.15] bg-transparent px-2 py-1 text-sm text-black dark:border-white/[.2] dark:text-zinc-50"
          />
          <Input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Note (optional)"
            className="text-xs"
          />
          <div className="flex gap-2">
            <Button variant="secondary" onClick={handleSave} disabled={saving || !email}>
              {saving ? "Saving…" : "Save"}
            </Button>
            {device.override_email && (
              <Button variant="danger" onClick={handleClear} disabled={saving}>
                Clear
              </Button>
            )}
          </div>
          {rowError && <ErrorState>{rowError}</ErrorState>}
          {lastResult && <p className="text-xs text-zinc-500">{lastResult}</p>}
        </div>
      </td>
    </tr>
  );
}

function AttributionAliasesSection({
  aliases,
  onChanged,
}: {
  aliases: AttributionAlias[];
  onChanged: () => void;
}) {
  const [alias, setAlias] = useState("");
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  async function handleAdd() {
    setSaving(true);
    setFormError(null);
    setLastResult(null);
    try {
      const result = await createAttributionAlias({ alias, resolved_email: email, note: note || null });
      setLastResult(
        result.backfilled_job_count > 0
          ? `Merged — updated ${result.backfilled_job_count} past job${result.backfilled_job_count === 1 ? "" : "s"}.`
          : "Merged.",
      );
      setAlias("");
      setEmail("");
      setNote("");
      onChanged();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to add alias");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    setSaving(true);
    try {
      await deleteAttributionAlias(id);
      onChanged();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to remove alias");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-1">Attribution Aliases</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Merge an arbitrary login string — a local computer username (e.g. &quot;matt&quot; instead
        of matt&apos;s real address) or an old/alternate email address — into one staff
        member&apos;s canonical email. Useful for cleanup or after a username change. Google
        Workspace&apos;s own account aliases (shown with a badge below) are merged automatically
        on every sync — this is for anything Google doesn&apos;t already know about.
      </p>

      {aliases.length > 0 && (
        <table className="mb-4 w-full text-left text-sm">
          <thead>
            <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
              <th className="py-2 font-medium">Alias</th>
              <th className="py-2 font-medium">Resolves To</th>
              <th className="py-2 font-medium">Source</th>
              <th className="py-2 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {aliases.map((a) => (
              <tr key={a.id} className="border-b border-black/[.06] last:border-0 dark:border-white/[.1]">
                <td className="py-2 font-mono text-xs">{a.alias}</td>
                <td className="py-2">{a.resolved_email}</td>
                <td className="py-2">
                  <Badge tone={a.source === "manual" ? "neutral" : "info"}>
                    {a.source === "manual" ? "Manual" : "Google Workspace"}
                  </Badge>
                </td>
                <td className="py-2 text-right">
                  <Button
                    variant="danger"
                    className="!px-2 !py-0.5 text-xs"
                    disabled={saving}
                    onClick={() => handleDelete(a.id)}
                  >
                    Remove
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="flex flex-wrap items-end gap-2">
        <Input value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="matt" className="max-w-[10rem]" />
        <input
          list={ROSTER_DATALIST_ID}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="real-user@domain.com"
          className="rounded border border-black/[.15] bg-transparent px-2 py-1.5 text-sm text-black dark:border-white/[.2] dark:text-zinc-50"
        />
        <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" className="max-w-[10rem]" />
        <Button onClick={handleAdd} disabled={saving || !alias || !email}>
          {saving ? "Merging…" : "Merge"}
        </Button>
      </div>
      {formError && <ErrorState>{formError}</ErrorState>}
      {lastResult && <p className="mt-2 text-xs text-emerald-700 dark:text-emerald-400">{lastResult}</p>}
    </Card>
  );
}

export default function DevicesPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [roster, setRoster] = useState<GoogleWorkspaceUserEntry[]>([]);
  const [aliases, setAliases] = useState<AttributionAlias[]>([]);

  useEffect(() => {
    if (currentUser === null) {
      router.replace("/login");
    } else if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  function load() {
    listKnownDevices()
      .then((devices) => setState({ phase: "ok", devices }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load devices",
        }),
      );
    listGoogleWorkspaceUsers()
      .then(setRoster)
      .catch(() => setRoster([]));
    listAttributionAliases()
      .then(setAliases)
      .catch(() => setAliases([]));
  }

  useEffect(() => {
    if (currentUser?.role === "admin") load();
  }, [currentUser]);

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="flex w-full max-w-5xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Devices</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Devices seen via Mosyle or Google Workspace. Mosyle/Google&rsquo;s own reported user can
          be a bare local username rather than an email — if that&rsquo;s ambiguous (e.g. shared by
          multiple people), set the correct email here. It&rsquo;s validated against the synced
          Google Workspace roster and immediately re-attributes this device&rsquo;s already-logged
          jobs.
        </p>
      </div>

      {state.phase === "loading" && <Spinner label="Loading devices…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && (
        <Card className="p-0">
          {state.devices.length === 0 ? (
            <div className="p-6">
              <EmptyState>No devices synced yet from Mosyle or Google Workspace.</EmptyState>
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                  <th className="px-4 py-3 font-medium">MAC Address</th>
                  <th className="px-4 py-3 font-medium">Source</th>
                  <th className="px-4 py-3 font-medium">Device</th>
                  <th className="px-4 py-3 font-medium">Reported User</th>
                  <th className="px-4 py-3 font-medium">Override Email</th>
                </tr>
              </thead>
              <tbody>
                {state.devices.map((device) => (
                  <DeviceRow key={device.mac_address} device={device} onSaved={load} />
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      <AttributionAliasesSection aliases={aliases} onChanged={load} />

      <datalist id={ROSTER_DATALIST_ID}>
        {roster.map((u) => (
          <option key={u.email} value={u.email}>
            {u.name ?? u.email}
          </option>
        ))}
      </datalist>
    </div>
  );
}
