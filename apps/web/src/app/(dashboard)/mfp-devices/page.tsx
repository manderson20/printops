"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listMfpDevices, type MfpDevice } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; devices: MfpDevice[] }
  | { phase: "error"; message: string };

const CAPABILITY_LABELS: Record<string, string> = {
  walkup_copy_accounting: "Walk-up copy",
  user_code_pin_auth: "User code/PIN",
  badge_card_auth: "Badge/card",
  department_id_accounting: "Department ID",
  api_accounting_retrieval: "API accounting",
  csv_accounting_export: "CSV export",
  snmp_meter_counters: "SNMP meter",
};

function capabilitySummary(device: MfpDevice): string[] {
  return Object.entries(CAPABILITY_LABELS)
    .filter(([key]) => device.capabilities[key as keyof typeof device.capabilities] === true)
    .map(([, label]) => label);
}

export default function MfpDevicesPage() {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [state, setState] = useState<LoadState>({ phase: "loading" });

  useEffect(() => {
    listMfpDevices()
      .then((devices) => setState({ phase: "ok", devices }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load MFP devices",
        }),
      );
  }, []);

  return (
    <div className="flex w-full max-w-5xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Copiers</h1>
        {isAdmin && (
          <div className="flex gap-2">
            <Link href="/staff-copier-identities">
              <Button variant="secondary">Staff Copier Identities</Button>
            </Link>
            <Link href="/copier-imports">
              <Button variant="secondary">Accounting Imports</Button>
            </Link>
            <Link href="/copier-unmapped">
              <Button variant="secondary">Unmapped Activity</Button>
            </Link>
            <Link href="/mfp-devices/new">
              <Button>Add MFP Device</Button>
            </Link>
          </div>
        )}
      </div>

      <p className="text-sm text-zinc-500">
        Walk-up copier/MFP accounting — tracks copy activity staff make directly at a device
        (badge, PIN, department ID, ...), separate from IPP print jobs. See Staff Copier
        Identities to map staff to their copier logins, and Accounting Imports to bring in a
        vendor&apos;s accounting export.
      </p>

      {state.phase === "loading" && <Spinner label="Loading MFP devices…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}
      {state.phase === "ok" && state.devices.length === 0 && (
        <EmptyState>No MFP devices yet. Add one to start tracking copier activity.</EmptyState>
      )}
      {state.phase === "ok" && state.devices.length > 0 && (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead className="bg-black/[.03] text-zinc-600 dark:bg-white/[.05] dark:text-zinc-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Name</th>
                  <th className="px-4 py-3 font-medium">Vendor / Model</th>
                  <th className="px-4 py-3 font-medium">Connector</th>
                  <th className="px-4 py-3 font-medium">Location</th>
                  <th className="px-4 py-3 font-medium">Capabilities</th>
                  <th className="px-4 py-3 font-medium">Last Test</th>
                </tr>
              </thead>
              <tbody>
                {state.devices.map((device) => (
                  <tr
                    key={device.id}
                    className="border-t border-black/[.08] hover:bg-black/[.02] dark:border-white/[.1] dark:hover:bg-white/[.03]"
                  >
                    <td className="px-4 py-3">
                      <Link
                        href={`/mfp-devices/${device.id}`}
                        className="font-medium text-black hover:underline dark:text-zinc-50"
                      >
                        {device.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {device.vendor} {device.model ?? ""}
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone="info">{device.connector_type}</Badge>
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {[device.building, device.room, device.department].filter(Boolean).join(" / ") ||
                        "—"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex max-w-xs flex-wrap gap-1">
                        {capabilitySummary(device).length === 0 ? (
                          <span className="text-xs text-zinc-400">Not assessed</span>
                        ) : (
                          capabilitySummary(device).map((label) => (
                            <Badge key={label} tone="success">
                              {label}
                            </Badge>
                          ))
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      {device.last_test_connection_at === null ? (
                        <span className="text-xs text-zinc-400">Never tested</span>
                      ) : (
                        <Badge tone={device.last_test_connection_ok ? "success" : "danger"}>
                          {device.last_test_connection_ok ? "OK" : "Failed"}
                        </Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
