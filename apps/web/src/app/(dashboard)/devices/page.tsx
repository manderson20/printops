"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  deleteDeviceOverride,
  listGoogleWorkspaceUsers,
  listKnownDevices,
  setDeviceOverride,
  type GoogleWorkspaceUserEntry,
  type KnownDevice,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

const PAGE_SIZE = 50;

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; devices: KnownDevice[]; total: number }
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

export default function DevicesPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [roster, setRoster] = useState<GoogleWorkspaceUserEntry[]>([]);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    if (currentUser === null) {
      router.replace("/login");
    } else if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  const load = useCallback(() => {
    listKnownDevices({ page, pageSize: PAGE_SIZE, search: search || undefined })
      .then((result) => setState({ phase: "ok", devices: result.items, total: result.total }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load devices",
        }),
      );
    listGoogleWorkspaceUsers()
      .then(setRoster)
      .catch(() => setRoster([]));
  }, [page, search]);

  useEffect(() => {
    if (currentUser?.role === "admin") load();
  }, [currentUser, load]);

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  }

  const total = state.phase === "ok" ? state.total : 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
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

      <div className="flex flex-wrap items-center gap-3">
        <form onSubmit={handleSearchSubmit} className="flex items-center gap-2">
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search MAC, device, or user…"
            className="w-full max-w-xs"
          />
          <Button type="submit" variant="secondary">
            Search
          </Button>
          {search && (
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setSearchInput("");
                setSearch("");
                setPage(1);
              }}
            >
              Clear
            </Button>
          )}
        </form>
        <span className="text-xs text-zinc-500">{total.toLocaleString()} devices</span>
      </div>

      {state.phase === "loading" && <Spinner label="Loading devices…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && (
        <Card className="overflow-hidden p-0">
          {state.devices.length === 0 ? (
            <div className="p-6">
              <EmptyState>
                {search
                  ? `No devices match "${search}".`
                  : "No devices synced yet from Mosyle or Google Workspace."}
              </EmptyState>
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

      {state.phase === "ok" && state.devices.length > 0 && (
        <div className="flex items-center justify-between text-xs text-zinc-500">
          <span>
            Page {page} of {totalPages}
          </span>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              className="!px-2 !py-1 text-xs"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              className="!px-2 !py-1 text-xs"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              Next
            </Button>
          </div>
        </div>
      )}

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
