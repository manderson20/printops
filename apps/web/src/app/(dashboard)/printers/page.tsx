"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, listPrinters, testPrintPrinter, type Printer } from "@/lib/api";
import { capabilityBadges } from "@/lib/capabilities";
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

export default function PrintersPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [testPrints, setTestPrints] = useState<Record<string, TestPrintState>>({});

  useEffect(() => {
    listPrinters()
      .then((printers) => setState({ phase: "ok", printers }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load printers",
        }),
      );
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
        <Link href="/printers/new">
          <Button>Add Printer</Button>
        </Link>
      </div>

      {state.phase === "loading" && <Spinner label="Loading printers…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}
      {state.phase === "ok" && state.printers.length === 0 && (
        <EmptyState>No printers yet. Add one to get started.</EmptyState>
      )}
      {state.phase === "ok" && state.printers.length > 0 && (
        <Card className="overflow-hidden p-0">
          <table className="w-full text-left text-sm">
            <thead className="bg-black/[.03] text-zinc-600 dark:bg-white/[.05] dark:text-zinc-400">
              <tr>
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Model</th>
                <th className="px-4 py-3 font-medium">IP Address</th>
                <th className="px-4 py-3 font-medium">Location</th>
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
                    {printer.airprint_enabled ? (
                      <Badge tone="success">Discoverable</Badge>
                    ) : (
                      <Badge tone="neutral">Hidden</Badge>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {capabilityBadges(printer.capabilities).map((badge) => (
                        <Badge key={badge} tone="info">
                          {badge}
                        </Badge>
                      ))}
                      {capabilityBadges(printer.capabilities).length === 0 && (
                        <span className="text-xs text-zinc-400">—</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col items-start gap-1">
                      <Button
                        variant="secondary"
                        className="!px-3 !py-1 text-xs"
                        disabled={testPrint?.phase === "sending"}
                        onClick={() => handleTestPrint(printer.id)}
                      >
                        {testPrint?.phase === "sending" ? "Sending…" : "Test Print"}
                      </Button>
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
        </Card>
      )}
    </div>
  );
}
