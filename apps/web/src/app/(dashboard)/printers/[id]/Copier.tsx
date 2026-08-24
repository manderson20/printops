"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  disablePrinterCopier,
  enablePrinterCopier,
  getPrinterCopier,
  type MfpDevice,
  type Printer,
} from "@/lib/api";
import { CopierPanel } from "./CopierPanel";
import { formatRelativeTime } from "@/lib/format";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";

/** The copier side of a machine, on the machine's own page.
 *
 * A copier and a printer were two records for one device, in two places, each
 * describing the same connection, model, location and meter. Every copier in
 * this district is also a printer, so the printer is the machine and copying is
 * something it can be given.
 *
 * Turning it off only hides the feature. Nothing about the copier is deleted —
 * every foreign key into the copier table cascades, and the district's
 * provisioned accounts, counter readings and usage history all hang off that
 * one row, so a toggle in the UI must never be a way to lose them. That is why
 * this says "stop tracking" rather than "remove".
 */
export function CopierCard({
  printer,
  onUpdate,
}: {
  printer: Printer;
  onUpdate: (printer: Printer) => void;
}) {
  const [device, setDevice] = useState<MfpDevice | null>(null);
  // Which printer the fetch above has finished for. Distinguishes "still
  // fetching" from "fetched, and there is nothing there" — without it, a
  // printer flagged as a copier whose record has gone (deleted through the
  // copier API, say) sat on "Loading copier details…" forever. Keyed by id
  // rather than reset at the top of the effect, because setting state
  // synchronously in an effect body just causes another render.
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const loaded = loadedFor === printer.id;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Only fetches; clearing on disable happens in the toggle handler, because
    // setting state synchronously in an effect body just causes another render.
    if (!printer.copier_enabled) return;
    getPrinterCopier(printer.id)
      .then(setDevice)
      .catch(() => setDevice(null))
      .finally(() => setLoadedFor(printer.id));
  }, [printer.id, printer.copier_enabled]);

  async function toggle(enable: boolean) {
    setBusy(true);
    setError(null);
    try {
      if (enable) {
        const created = await enablePrinterCopier(printer.id);
        setDevice(created);
        onUpdate({ ...printer, copier_enabled: true });
      } else {
        onUpdate(await disablePrinterCopier(printer.id));
        setDevice(null);
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not change this setting",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="mb-1">Copier</CardTitle>
            <p className="text-sm text-zinc-500">
              {printer.copier_enabled
                ? "This machine is tracked as a copier: walk-up copying, per-account counters and staff codes."
                : "Turn this on if people also walk up to this machine and copy. PrintOps will then track copies per person alongside their printing."}
            </p>
          </div>
          <Button
            variant={printer.copier_enabled ? "secondary" : "primary"}
            disabled={busy}
            onClick={() => toggle(!printer.copier_enabled)}
          >
            {busy
              ? "Saving…"
              : printer.copier_enabled
                ? "Stop tracking copies"
                : "Track copies"}
          </Button>
        </div>

        {error && (
          <div className="mt-3">
            <ErrorState>{error}</ErrorState>
          </div>
        )}

        {printer.copier_enabled && device && (
          <div className="mt-4 flex flex-col gap-3 border-t border-black/[.08] pt-4 dark:border-white/[.145]">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone="info">{device.connector_type}</Badge>
              {device.serial_number && (
                <span className="text-zinc-500">
                  Serial {device.serial_number}
                </span>
              )}
              {device.page_count_total != null && (
                <span className="text-zinc-500">
                  Meter {device.page_count_total.toLocaleString()}
                  {device.page_count_checked_at &&
                    ` · read ${formatRelativeTime(device.page_count_checked_at)}`}
                </span>
              )}
            </div>
          </div>
        )}

        {printer.copier_enabled && !device && !loaded && (
          <p className="mt-3 text-sm text-zinc-500">Loading copier details…</p>
        )}

        {printer.copier_enabled && !device && loaded && (
          <p className="mt-3 text-sm text-zinc-500">
            This printer is marked as a copier, but its copier record is missing.
            Turning copy tracking off and on again will create a fresh one — any
            previous accounting stayed with the record that was removed.
          </p>
        )}
      </Card>

      {/* The copier's own settings live below the switch rather than a
          click away: copy tracking, staff accounts, counters and owner
          attribution. Kept outside the card above so these read as the
          machine's settings, not as detail nested under a toggle. */}
      {printer.copier_enabled && device && (
        <CopierPanel deviceId={device.id} printer={printer} />
      )}
    </div>
  );
}
