"use client";

import { useEffect, useState } from "react";
import { ApiError, getMdmConnection, resyncQueue, type MdmConnectionInfo } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { usePrinterDetail } from "../PrinterDetailContext";

export default function ConnectionTab() {
  const { printer, setPrinter } = usePrinterDetail();
  const isAdmin = useCurrentUser()?.role === "admin";
  const [connection, setConnection] = useState<MdmConnectionInfo | null>(null);
  const [resyncing, setResyncing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  useEffect(() => {
    getMdmConnection(printer.id)
      .then(setConnection)
      .catch(() => setConnection(null));
  }, [printer.id]);

  async function handleCopy(field: string, value: string) {
    await navigator.clipboard.writeText(value);
    setCopiedField(field);
    setTimeout(() => setCopiedField((current) => (current === field ? null : current)), 1500);
  }

  async function handleResync() {
    setResyncing(true);
    setActionError(null);
    try {
      const updated = await resyncQueue(printer.id);
      setPrinter(updated);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Queue resync failed");
    } finally {
      setResyncing(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <div className="mb-1 flex items-center justify-between">
          <CardTitle>Connection Info</CardTitle>
          {isAdmin && (
            <Button variant="secondary" onClick={handleResync} disabled={resyncing}>
              {resyncing ? "Resyncing…" : "Resync Queue"}
            </Button>
          )}
        </div>
        <p className="mb-4 text-xs text-zinc-500">
          For manually adding this printer&apos;s PrintOps queue in an MDM tool (e.g. Mosyle).
          This points at the PrintOps server, not the printer itself — clients print through the
          proxy.
        </p>

        {printer.queue_sync_error && (
          <div className="mb-4 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
            <p className="font-medium">CUPS queue is out of sync.</p>
            <p className="mt-1 text-amber-800 dark:text-amber-300">{printer.queue_sync_error}</p>
            <p className="mt-1 text-amber-800 dark:text-amber-300">
              This printer won&apos;t accept jobs until this is fixed. Common cause: the printer
              was unreachable when PrintOps last tried to sync the queue — check connectivity,
              then click Resync Queue above.
            </p>
          </div>
        )}

        {actionError && <ErrorState>{actionError}</ErrorState>}

        {connection === null && <Spinner label="Loading connection info…" />}
        {connection && (
          <div className="flex flex-col gap-2 text-sm">
            {[
              ["IPP URI", connection.ipp_uri],
              ["Host", connection.host],
              ["Port", String(connection.port)],
              ["Queue / Resource Path", connection.resource_path],
            ].map(([label, value]) => (
              <div
                key={label}
                className="flex items-center justify-between gap-3 border-t border-black/[.08] pt-2 first:border-t-0 first:pt-0 dark:border-white/[.1]"
              >
                <div className="flex flex-col">
                  <span className="text-xs font-medium text-zinc-500">{label}</span>
                  <code className="text-zinc-800 dark:text-zinc-200">{value}</code>
                </div>
                <Button
                  variant="secondary"
                  className="!px-3 !py-1 text-xs"
                  onClick={() => handleCopy(label, value)}
                >
                  {copiedField === label ? "Copied" : "Copy"}
                </Button>
              </div>
            ))}
            <div className="flex items-center justify-between gap-3 border-t border-black/[.08] pt-2 dark:border-white/[.1]">
              <div className="flex flex-col">
                <span className="text-xs font-medium text-zinc-500">TLS</span>
                <span className="text-zinc-800 dark:text-zinc-200">
                  Off — the IPP URI above uses ipp://, not ipps://
                </span>
              </div>
            </div>
          </div>
        )}
      </Card>

      <Card>
        <CardTitle className="mb-1">iPad AirPrint MDM Profile</CardTitle>
        <p className="mb-4 text-xs text-zinc-500">
          iPadOS doesn&apos;t support &quot;paste one IPP URI&quot; like the macOS queue above —
          it needs an AirPrint payload with these four fields (in Mosyle: Devices → Printer
          Management → Add AirPrint). Cross-VLAN Bonjour/mDNS discovery isn&apos;t reliable on
          this network, so this MDM-pushed profile is the way to get a printer onto iPads at all,
          same reason the macOS queue above bypasses discovery too.
        </p>

        {connection === null && <Spinner label="Loading connection info…" />}
        {connection && (
          <div className="flex flex-col gap-2 text-sm">
            {[
              ["Host Name or IP Address", connection.host],
              ["Resource Path", connection.resource_path],
              ["Port Number", String(connection.port)],
            ].map(([label, value]) => (
              <div
                key={label}
                className="flex items-center justify-between gap-3 border-t border-black/[.08] pt-2 first:border-t-0 first:pt-0 dark:border-white/[.1]"
              >
                <div className="flex flex-col">
                  <span className="text-xs font-medium text-zinc-500">{label}</span>
                  <code className="text-zinc-800 dark:text-zinc-200">{value}</code>
                </div>
                <Button
                  variant="secondary"
                  className="!px-3 !py-1 text-xs"
                  onClick={() => handleCopy(`AirPrint ${label}`, value)}
                >
                  {copiedField === `AirPrint ${label}` ? "Copied" : "Copy"}
                </Button>
              </div>
            ))}
            <div className="flex items-center justify-between gap-3 border-t border-black/[.08] pt-2 dark:border-white/[.1]">
              <div className="flex flex-col">
                <span className="text-xs font-medium text-zinc-500">Force TLS</span>
                <span className="text-zinc-800 dark:text-zinc-200">Leave unchecked</span>
              </div>
            </div>
            <p className="mt-1 text-xs text-zinc-500">
              PrintOps serves this queue over plain IPP, not IPPS — same as the macOS queue
              above, so Force TLS should stay off.
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}
