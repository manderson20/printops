"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, listPrinters, testPrintPrinter, type Printer } from "@/lib/api";
import { capabilityBadges } from "@/lib/capabilities";
import { formatRelativeTime } from "@/lib/format";
import { printerStatusInfo } from "@/lib/printerStatus";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; printers: Printer[] }
  | { phase: "error"; message: string };

type TestPrintState = { phase: "sending" } | { phase: "ok" } | { phase: "error"; message: string };

// Multifunction copiers can report a dozen+ finishing options (staple,
// punch-top-left, punch-dual-right, ...) — showing all of them inline blows
// up the row height once several printers are added. Collapse to this many
// and let the admin expand per-row if they actually need the full list.
const VISIBLE_CAPABILITY_COUNT = 4;

export default function PrintersPage() {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [testPrints, setTestPrints] = useState<Record<string, TestPrintState>>({});
  const [expandedCapabilities, setExpandedCapabilities] = useState<Record<string, boolean>>({});

  useEffect(() => {
    listPrinters()
      .then((printers) => setState({ phase: "ok", printers }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load printers",
        }),
      );

    // The API polls each printer's status every 60s in the background
    // (app/main.py); re-fetch periodically here so the list reflects that
    // without a manual reload. Silent — doesn't reset to the loading phase,
    // so it never interrupts whatever the admin is doing on this page.
    const interval = setInterval(() => {
      listPrinters()
        .then((printers) => setState({ phase: "ok", printers }))
        .catch(() => {});
    }, 30_000);
    return () => clearInterval(interval);
  }, []);

  async function handleTestPrint(printerId: string) {
    setTestPrints((prev) => ({ ...prev, [printerId]: { phase: "sending" } }));
    try {
      await testPrintPrinter(printerId);
      setTestPrints((prev) => ({ ...prev, [printerId]: { phase: "ok" } }));
    } catch (err) {
      setTestPrints((prev) => ({
        ...prev,
        [printerId]: {
          phase: "error",
          message: err instanceof ApiError ? err.message : "Test print failed",
        },
      }));
    }
  }

  return (
    <div className="flex w-full max-w-5xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Printers</h1>
        {isAdmin && (
          <Link href="/printers/new">
            <Button>Add Printer</Button>
          </Link>
        )}
      </div>

      {state.phase === "loading" && <Spinner label="Loading printers…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}
      {state.phase === "ok" && state.printers.length === 0 && (
        <EmptyState>No printers yet. Add one to get started.</EmptyState>
      )}
      {state.phase === "ok" && state.printers.length > 0 && (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
          <table className="w-full min-w-[1100px] text-left text-sm">
            <thead className="bg-black/[.03] text-zinc-600 dark:bg-white/[.05] dark:text-zinc-400">
              <tr>
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Pages</th>
                <th className="px-4 py-3 font-medium">Model</th>
                <th className="px-4 py-3 font-medium">IP Address</th>
                <th className="px-4 py-3 font-medium">Location</th>
                <th className="px-4 py-3 font-medium">Queue</th>
                <th className="px-4 py-3 font-medium">AirPrint</th>
                <th className="px-4 py-3 font-medium">Capabilities</th>
                <th className="px-4 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {state.printers.map((printer) => {
                const testPrint = testPrints[printer.id];
                return (
                <tr
                  key={printer.id}
                  className="border-t border-black/[.08] hover:bg-black/[.02] dark:border-white/[.1] dark:hover:bg-white/[.03]"
                >
                  <td className="px-4 py-3">
                    <Link
                      href={`/printers/${printer.id}`}
                      className="font-medium text-black hover:underline dark:text-zinc-50"
                    >
                      {printer.name}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    {(() => {
                      const info = printerStatusInfo(printer.status);
                      const title = [
                        printer.status_message,
                        ...(printer.status_reasons ?? []),
                      ]
                        .filter(Boolean)
                        .join(", ");
                      return (
                        <div className="flex flex-col gap-0.5">
                          <Badge tone={info.tone} title={title || undefined}>
                            {info.label}
                          </Badge>
                          <span className="text-xs text-zinc-400">
                            {formatRelativeTime(printer.status_checked_at)}
                          </span>
                        </div>
                      );
                    })()}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {printer.page_count_total ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {printer.manufacturer ?? ""} {printer.model ?? "—"}
                    {printer.capabilities_error && (
                      <span className="ml-2 text-amber-700 dark:text-amber-400">
                        (capabilities not detected)
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {printer.ip_address}
                  </td>
                  <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {[printer.building, printer.room, printer.department]
                      .filter(Boolean)
                      .join(" / ") || "—"}
                  </td>
                  <td className="px-4 py-3">
                    {printer.queue_sync_error ? (
                      <Badge tone="danger" title={printer.queue_sync_error}>
                        Sync Failed
                      </Badge>
                    ) : (
                      <Badge tone="success">Synced</Badge>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {printer.airprint_enabled ? (
                      <Badge tone="success">Discoverable</Badge>
                    ) : (
                      <Badge tone="neutral">Hidden</Badge>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {(() => {
                      const badges = capabilityBadges(printer.capabilities);
                      if (badges.length === 0) {
                        return <span className="text-xs text-zinc-400">—</span>;
                      }
                      const expanded = expandedCapabilities[printer.id] ?? false;
                      const visible = expanded ? badges : badges.slice(0, VISIBLE_CAPABILITY_COUNT);
                      const hiddenCount = badges.length - visible.length;
                      return (
                        <div className="flex max-w-xs flex-wrap items-center gap-1">
                          {visible.map((badge) => (
                            <Badge key={badge} tone="info">
                              {badge}
                            </Badge>
                          ))}
                          {(hiddenCount > 0 || expanded) && badges.length > VISIBLE_CAPABILITY_COUNT && (
                            <button
                              onClick={() =>
                                setExpandedCapabilities((prev) => ({
                                  ...prev,
                                  [printer.id]: !expanded,
                                }))
                              }
                              className="text-xs font-medium text-accent hover:underline"
                            >
                              {expanded ? "Show less" : `+${hiddenCount} more`}
                            </button>
                          )}
                        </div>
                      );
                    })()}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col items-start gap-1">
                      {isAdmin && (
                        <Button
                          variant="secondary"
                          className="!px-3 !py-1 text-xs"
                          disabled={testPrint?.phase === "sending"}
                          onClick={() => handleTestPrint(printer.id)}
                        >
                          {testPrint?.phase === "sending" ? "Sending…" : "Test Print"}
                        </Button>
                      )}
                      {testPrint?.phase === "ok" && (
                        <span className="text-xs text-emerald-700 dark:text-emerald-400">
                          Sent — check Jobs
                        </span>
                      )}
                      {testPrint?.phase === "error" && (
                        <span className="max-w-[16rem] text-xs text-red-600 dark:text-red-400">
                          {testPrint.message}
                        </span>
                      )}
                    </div>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </Card>
      )}
    </div>
  );
}
