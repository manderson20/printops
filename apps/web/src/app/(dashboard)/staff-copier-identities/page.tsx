"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  createStaffCopierIdentity,
  deleteStaffCopierIdentity,
  listGoogleWorkspaceUsers,
  listMfpDevices,
  listStaffCopierIdentities,
  listStaffMissingCopierIdentity,
  type CopierIdentityType,
  type GoogleWorkspaceUserEntry,
  type MfpDevice,
  type StaffCopierIdentity,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

const IDENTITY_TYPES: { value: CopierIdentityType; label: string }[] = [
  { value: "staff_id", label: "Staff ID" },
  { value: "pin", label: "PIN" },
  { value: "badge_id", label: "Badge / Card ID" },
  { value: "department_id", label: "Department ID" },
  { value: "user_code", label: "Vendor User Code" },
  { value: "vendor_user_id", label: "Vendor User ID" },
  { value: "email", label: "Email" },
];

function StaffIdentityRow({
  user,
  identities,
  devices,
  onChanged,
}: {
  user: GoogleWorkspaceUserEntry;
  identities: StaffCopierIdentity[];
  devices: MfpDevice[];
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [identityType, setIdentityType] = useState<CopierIdentityType>("staff_id");
  const [identityValue, setIdentityValue] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [saving, setSaving] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);

  async function handleAdd() {
    setSaving(true);
    setRowError(null);
    try {
      await createStaffCopierIdentity({
        staff_email: user.email,
        identity_type: identityType,
        identity_value: identityValue,
        mfp_device_id: deviceId || null,
      });
      setIdentityValue("");
      onChanged();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to add identity");
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove(id: string) {
    setSaving(true);
    setRowError(null);
    try {
      await deleteStaffCopierIdentity(id);
      onChanged();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to remove identity");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="border-b border-black/[.08] last:border-0 dark:border-white/[.145]">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-black/[.02] dark:hover:bg-white/[.03]"
      >
        <div>
          <p className="font-medium text-black dark:text-zinc-50">{user.name ?? user.email}</p>
          <p className="text-xs text-zinc-500">
            {user.email}
            {user.employee_id && ` · Employee ID ${user.employee_id}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {identities.length === 0 ? (
            <Badge tone="warning">No copier identities</Badge>
          ) : (
            <Badge tone="success">{identities.length} identity{identities.length === 1 ? "" : "ies"}</Badge>
          )}
          <span className="text-xs text-zinc-400">{expanded ? "▲" : "▼"}</span>
        </div>
      </button>

      {expanded && (
        <div className="flex flex-col gap-3 px-4 pb-4">
          {identities.length > 0 && (
            <table className="w-full text-left text-xs">
              <thead className="text-zinc-500">
                <tr>
                  <th className="py-1 font-medium">Type</th>
                  <th className="py-1 font-medium">Value</th>
                  <th className="py-1 font-medium">Device Scope</th>
                  <th className="py-1 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {identities.map((identity) => (
                  <tr key={identity.id} className="border-t border-black/[.06] dark:border-white/[.08]">
                    <td className="py-1.5">
                      {IDENTITY_TYPES.find((t) => t.value === identity.identity_type)?.label ??
                        identity.identity_type}
                    </td>
                    <td className="py-1.5 font-mono">{identity.identity_value}</td>
                    <td className="py-1.5 text-zinc-500">
                      {identity.mfp_device_id
                        ? devices.find((d) => d.id === identity.mfp_device_id)?.name ?? "Device"
                        : "Org-wide"}
                    </td>
                    <td className="py-1.5 text-right">
                      <Button
                        variant="danger"
                        className="!px-2 !py-0.5 text-xs"
                        disabled={saving}
                        onClick={() => handleRemove(identity.id)}
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
            <select
              value={identityType}
              onChange={(e) => setIdentityType(e.target.value as CopierIdentityType)}
              className="rounded border border-black/[.15] bg-transparent px-2 py-1 text-xs dark:border-white/[.2]"
            >
              {IDENTITY_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
            <Input
              value={identityValue}
              onChange={(e) => setIdentityValue(e.target.value)}
              placeholder="Value"
              className="!py-1 text-xs"
            />
            <select
              value={deviceId}
              onChange={(e) => setDeviceId(e.target.value)}
              className="rounded border border-black/[.15] bg-transparent px-2 py-1 text-xs dark:border-white/[.2]"
            >
              <option value="">Org-wide</option>
              {devices.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
            <Button
              variant="secondary"
              className="!px-3 !py-1 text-xs"
              disabled={saving || !identityValue}
              onClick={handleAdd}
            >
              {saving ? "Adding…" : "Add"}
            </Button>
          </div>
          {rowError && <ErrorState>{rowError}</ErrorState>}
        </div>
      )}
    </div>
  );
}

export default function StaffCopierIdentitiesPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [roster, setRoster] = useState<GoogleWorkspaceUserEntry[] | null>(null);
  const [identities, setIdentities] = useState<StaffCopierIdentity[]>([]);
  const [devices, setDevices] = useState<MfpDevice[]>([]);
  const [missingOnly, setMissingOnly] = useState(false);
  const [missingEmails, setMissingEmails] = useState<Set<string> | null>(null);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  function load() {
    listGoogleWorkspaceUsers()
      .then(setRoster)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load roster"));
    listStaffCopierIdentities()
      .then(setIdentities)
      .catch(() => setIdentities([]));
    listMfpDevices()
      .then(setDevices)
      .catch(() => setDevices([]));
    listStaffMissingCopierIdentity()
      .then((rows) => setMissingEmails(new Set(rows.map((r) => r.email.toLowerCase()))))
      .catch(() => setMissingEmails(new Set()));
  }

  useEffect(() => {
    if (currentUser?.role === "admin") load();
  }, [currentUser]);

  const identitiesByEmail = useMemo(() => {
    const map = new Map<string, StaffCopierIdentity[]>();
    for (const identity of identities) {
      const key = identity.staff_email.toLowerCase();
      map.set(key, [...(map.get(key) ?? []), identity]);
    }
    return map;
  }, [identities]);

  const visibleRoster = useMemo(() => {
    if (!roster) return [];
    return roster.filter((u) => {
      if (missingOnly && !missingEmails?.has(u.email.toLowerCase())) return false;
      if (!search) return true;
      const haystack = `${u.name ?? ""} ${u.email}`.toLowerCase();
      return haystack.includes(search.toLowerCase());
    });
  }, [roster, missingOnly, missingEmails, search]);

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Staff Copier Identities</h1>
          <WikiHelpLink page="Copier-Accounting" anchor="staff-identities" />
        </div>
        <p className="mt-1 text-sm text-zinc-500">
          Map each staff member to the login(s) they use at walk-up copiers — staff ID, PIN,
          badge/card, or a vendor-specific code. A device scope restricts an identity to one
          specific copier (e.g. a Department ID configured locally); leave org-wide for
          anything valid everywhere (e.g. a badge).
        </p>
      </div>

      <div className="flex items-center gap-3">
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search staff by name or email…"
          className="max-w-xs"
        />
        <label className="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300">
          <input
            type="checkbox"
            checked={missingOnly}
            onChange={(e) => setMissingOnly(e.target.checked)}
          />
          Missing identity only
          {missingEmails && (
            <Badge tone="warning">{missingEmails.size}</Badge>
          )}
        </label>
      </div>

      {error && <ErrorState>{error}</ErrorState>}
      {roster === null && !error && <Spinner label="Loading staff roster…" />}
      {roster !== null && visibleRoster.length === 0 && (
        <EmptyState>
          {missingOnly ? "Every synced staff member has a copier identity." : "No staff match this search."}
        </EmptyState>
      )}
      {roster !== null && visibleRoster.length > 0 && (
        <Card className="p-0">
          {visibleRoster.map((user) => (
            <StaffIdentityRow
              key={user.email}
              user={user}
              identities={identitiesByEmail.get(user.email.toLowerCase()) ?? []}
              devices={devices}
              onChanged={load}
            />
          ))}
        </Card>
      )}
    </div>
  );
}
