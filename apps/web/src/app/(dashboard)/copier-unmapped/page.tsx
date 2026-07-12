"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  listGoogleWorkspaceUsers,
  listMfpDevices,
  listUnmappedCopierActivity,
  resolveUnmappedCopierActivity,
  type CopierIdentityType,
  type GoogleWorkspaceUserEntry,
  type MfpDevice,
  type UnmappedCopierIdentityGroup,
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

const ROSTER_DATALIST_ID = "google-workspace-roster-unmapped";

const IDENTITY_TYPES: { value: CopierIdentityType; label: string }[] = [
  { value: "staff_id", label: "Staff ID" },
  { value: "pin", label: "PIN" },
  { value: "badge_id", label: "Badge / Card ID" },
  { value: "department_id", label: "Department ID" },
  { value: "user_code", label: "Vendor User Code" },
  { value: "vendor_user_id", label: "Vendor User ID" },
  { value: "email", label: "Email" },
];

function UnmappedRow({
  group,
  deviceName,
  onResolved,
}: {
  group: UnmappedCopierIdentityGroup;
  deviceName: string;
  onResolved: () => void;
}) {
  const [identityType, setIdentityType] = useState<CopierIdentityType>(
    group.attempted_identity_type ?? "staff_id",
  );
  const [orgWide, setOrgWide] = useState(false);
  const [email, setEmail] = useState("");
  const [saving, setSaving] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  async function handleResolve() {
    setSaving(true);
    setRowError(null);
    setLastResult(null);
    try {
      const result = await resolveUnmappedCopierActivity({
        mfp_device_id: orgWide ? null : group.mfp_device_id,
        identity_type: identityType,
        identity_value: group.external_identity_used,
        resolved_email: email,
      });
      setLastResult(
        result.backfilled_row_count > 0
          ? `Resolved — updated ${result.backfilled_row_count} past record${result.backfilled_row_count === 1 ? "" : "s"}.`
          : "Resolved.",
      );
      onResolved();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to resolve");
    } finally {
      setSaving(false);
    }
  }

  return (
    <tr className="border-b border-black/[.08] last:border-0 align-top dark:border-white/[.145]">
      <td className="px-4 py-3 font-mono text-sm text-black dark:text-zinc-50">
        {group.external_identity_used}
      </td>
      <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">{deviceName}</td>
      <td className="px-4 py-3">
        <Badge tone="warning">{group.occurrence_count}</Badge>
      </td>
      <td className="px-4 py-3 text-xs text-zinc-500">
        {formatRelativeTime(group.first_seen)} – {formatRelativeTime(group.last_seen)}
      </td>
      <td className="px-4 py-3">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
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
            <label className="flex items-center gap-1 text-xs text-zinc-600 dark:text-zinc-400">
              <input type="checkbox" checked={orgWide} onChange={(e) => setOrgWide(e.target.checked)} />
              Org-wide
            </label>
          </div>
          <input
            list={ROSTER_DATALIST_ID}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="staff@domain.com"
            className="rounded border border-black/[.15] bg-transparent px-2 py-1 text-sm dark:border-white/[.2]"
          />
          <Button variant="secondary" className="!px-3 !py-1 text-xs" onClick={handleResolve} disabled={saving || !email}>
            {saving ? "Resolving…" : "Assign & Reprocess"}
          </Button>
          {rowError && <ErrorState>{rowError}</ErrorState>}
          {lastResult && <p className="text-xs text-emerald-700 dark:text-emerald-400">{lastResult}</p>}
        </div>
      </td>
    </tr>
  );
}

export default function CopierUnmappedPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [groups, setGroups] = useState<UnmappedCopierIdentityGroup[] | null>(null);
  const [devices, setDevices] = useState<Record<string, MfpDevice>>({});
  const [roster, setRoster] = useState<GoogleWorkspaceUserEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (currentUser === null) {
      router.replace("/login");
    } else if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  function load() {
    listUnmappedCopierActivity()
      .then(setGroups)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load unmapped activity"));
    listMfpDevices()
      .then((list) => setDevices(Object.fromEntries(list.map((d) => [d.id, d]))))
      .catch(() => setDevices({}));
    listGoogleWorkspaceUsers().then(setRoster).catch(() => setRoster([]));
  }

  useEffect(() => {
    if (currentUser?.role === "admin") load();
  }, [currentUser]);

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Unmapped Copier Activity</h1>
          <WikiHelpLink page="Copier-Accounting" anchor="unmapped-activity" />
        </div>
        <p className="mt-1 text-sm text-zinc-500">
          Copier usage rows where the raw identity used at the device (staff ID, badge, PIN,
          department code, ...) didn&apos;t match anyone in Staff Copier Identities. Assigning
          one here creates that identity mapping and immediately backfills every past record
          that used it — not just future imports.
        </p>
      </div>

      {error && <ErrorState>{error}</ErrorState>}
      {groups === null && !error && <Spinner label="Loading unmapped activity…" />}
      {groups !== null && groups.length === 0 && (
        <EmptyState>No unmapped copier activity — everything resolves to a known staff member.</EmptyState>
      )}
      {groups !== null && groups.length > 0 && (
        <Card className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead>
                <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                  <th className="px-4 py-3 font-medium">Identity Used</th>
                  <th className="px-4 py-3 font-medium">Device</th>
                  <th className="px-4 py-3 font-medium">Count</th>
                  <th className="px-4 py-3 font-medium">Seen</th>
                  <th className="px-4 py-3 font-medium">Assign to Staff</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <UnmappedRow
                    key={`${group.mfp_device_id}-${group.external_identity_used}`}
                    group={group}
                    deviceName={devices[group.mfp_device_id]?.name ?? group.mfp_device_id}
                    onResolved={load}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
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
