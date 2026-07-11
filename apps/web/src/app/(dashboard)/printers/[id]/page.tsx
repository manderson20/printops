"use client";

import { useState } from "react";
import {
  ApiError,
  checkPrinterStatus,
  rediscoverPrinter,
  updatePrinter,
} from "@/lib/api";
import { capabilityBadges } from "@/lib/capabilities";
import { formatRelativeTime } from "@/lib/format";
import { printerStatusInfo } from "@/lib/printerStatus";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { usePrinterDetail } from "./PrinterDetailContext";
import { SnmpCountersCard } from "./SnmpCounters";
import { UsageHistoryCard } from "./UsageHistory";

const EDITABLE_FIELDS = [
  ["name", "Name"],
  ["ip_address", "IP Address"],
  ["manufacturer", "Manufacturer"],
  ["model", "Model"],
  ["hostname", "Hostname"],
  ["serial_number", "Serial Number"],
  ["building", "Building"],
  ["room", "Room"],
  ["department", "Department"],
  ["notes", "Notes"],
] as const;

// A virtual Follow-Me queue has no real device — IP/manufacturer/model/
// hostname/serial number are all meaningless for one.
const VIRTUAL_EDITABLE_FIELDS = [
  ["name", "Name"],
  ["building", "Building"],
  ["room", "Room"],
  ["department", "Department"],
  ["notes", "Notes"],
] as const;

export default function PrinterOverviewTab() {
  const { printer, setPrinter } = usePrinterDetail();
  const isAdmin = useCurrentUser()?.role === "admin";
  const editableFields = printer.is_virtual ? VIRTUAL_EDITABLE_FIELDS : EDITABLE_FIELDS;
  const [form, setForm] = useState<Record<string, string>>(
    Object.fromEntries(editableFields.map(([field]) => [field, (printer as never)[field] ?? ""])),
  );
  const [airprintEnabled, setAirprintEnabled] = useState(printer.airprint_enabled);
  const [useTls, setUseTls] = useState(printer.use_tls);
  const [saving, setSaving] = useState(false);
  const [rediscovering, setRediscovering] = useState(false);
  const [checkingStatus, setCheckingStatus] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const caps = printer.capabilities;

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      const updated = await updatePrinter(printer.id, {
        ...form,
        airprint_enabled: airprintEnabled,
        use_tls: useTls,
      });
      setPrinter(updated);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to save changes");
    } finally {
      setSaving(false);
    }
  }

  async function handleRediscover() {
    setRediscovering(true);
    setActionError(null);
    try {
      const updated = await rediscoverPrinter(printer.id);
      setPrinter(updated);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Rediscovery failed");
    } finally {
      setRediscovering(false);
    }
  }

  async function handleCheckStatus() {
    setCheckingStatus(true);
    setActionError(null);
    try {
      const updated = await checkPrinterStatus(printer.id);
      setPrinter(updated);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Status check failed");
    } finally {
      setCheckingStatus(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {printer.is_virtual && (
        <Card>
          <p className="text-sm text-zinc-500">
            This is a virtual Follow-Me queue — it has no real device behind it, so there&apos;s
            no status, capabilities, or usage counters to show here. Jobs sent to it are always
            held and released at whichever real printer the person walks up to.
          </p>
        </Card>
      )}

      {!printer.is_virtual && (
      <Card>
        <div className="mb-4 flex items-center justify-between">
          <CardTitle>Status</CardTitle>
          <Button variant="secondary" onClick={handleCheckStatus} disabled={checkingStatus}>
            {checkingStatus ? "Checking…" : "Check Now"}
          </Button>
        </div>
        {(() => {
          const info = printerStatusInfo(printer.status);
          return (
            <div className="flex flex-col gap-2 text-sm">
              <div className="flex items-center gap-2">
                <Badge tone={info.tone}>{info.label}</Badge>
                <span className="text-xs text-zinc-400">
                  Checked {formatRelativeTime(printer.status_checked_at)}
                </span>
              </div>
              {printer.status_message && (
                <p className="text-zinc-600 dark:text-zinc-400">{printer.status_message}</p>
              )}
              {printer.status_reasons && printer.status_reasons.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {printer.status_reasons.map((reason) => (
                    <Badge key={reason} tone="danger">
                      {reason}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          );
        })()}
      </Card>
      )}

      <Card>
        <CardTitle className="mb-4">Details</CardTitle>
        <div className="grid grid-cols-2 gap-4">
          {editableFields.map(([field, label]) => (
            <Field key={field} label={label}>
              <Input
                value={form[field] ?? ""}
                disabled={!isAdmin}
                onChange={(e) => setForm((prev) => ({ ...prev, [field]: e.target.value }))}
              />
            </Field>
          ))}
        </div>

        <label className="mt-4 flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
          <input
            type="checkbox"
            className="mt-1"
            checked={airprintEnabled}
            disabled={!isAdmin}
            onChange={(e) => setAirprintEnabled(e.target.checked)}
          />
          <span>
            Discoverable via AirPrint (Bonjour)
            <br />
            <span className="text-xs text-zinc-500">
              Off = hidden from automatic discovery on Macs/iPads; only devices explicitly
              configured (e.g. via MDM) can print to it. Recommended off for printers handling
              confidential documents.
            </span>
          </span>
        </label>

        {!printer.is_virtual && (
          <label className="mt-4 flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              className="mt-1"
              checked={useTls}
              disabled={!isAdmin}
              onChange={(e) => setUseTls(e.target.checked)}
            />
            <span>
              Connect via TLS (IPPS)
              <br />
              <span className="text-xs text-zinc-500">
                Encrypts traffic between PrintOps and this printer. Most office printers use a
                self-signed certificate, so this isn&apos;t strong endpoint verification, and not
                every device handles IPPS cleanly — only turn on if you&apos;ve confirmed this
                printer supports it.
              </span>
            </span>
          </label>
        )}

        {actionError && <ErrorState>{actionError}</ErrorState>}
        {isAdmin && (
          <Button onClick={handleSave} disabled={saving} className="mt-4">
            {saving ? "Saving…" : "Save"}
          </Button>
        )}
      </Card>

      {!printer.is_virtual && (
      <Card>
        <div className="mb-4 flex items-center justify-between">
          <CardTitle>Discovered Capabilities</CardTitle>
          {isAdmin && (
            <Button variant="secondary" onClick={handleRediscover} disabled={rediscovering}>
              {rediscovering ? "Probing…" : "Rediscover"}
            </Button>
          )}
        </div>

        {printer.capabilities_error && (
          <div className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
            <p className="font-medium">Printer saved, but capabilities couldn&apos;t be detected.</p>
            <p className="mt-1 text-amber-800 dark:text-amber-300">{printer.capabilities_error}</p>
            <p className="mt-1 text-amber-800 dark:text-amber-300">
              Common cause: IPP is disabled on the device by default (true for most Canon, and
              many other, printers) — enable it in the printer&apos;s own admin page, then click
              Rediscover below.
            </p>
          </div>
        )}

        {!caps && !printer.capabilities_error && (
          <EmptyState>No capabilities detected yet.</EmptyState>
        )}

        {caps?.tls_supported && !printer.use_tls && (
          <div className="mb-3 rounded border border-blue-300 bg-blue-50 p-3 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200">
            This printer advertises IPPS (TLS) support but it&apos;s not turned on — enable
            &quot;Connect via TLS (IPPS)&quot; above to encrypt traffic to it.
          </div>
        )}

        {caps && (
          <div className="flex flex-col gap-3 text-sm">
            <div className="flex flex-wrap gap-1">
              {capabilityBadges(caps).map((badge) => (
                <Badge key={badge} tone="info">
                  {badge}
                </Badge>
              ))}
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-zinc-600 dark:text-zinc-400">
              <dt className="font-medium text-zinc-500">Make/Model</dt>
              <dd>{caps.make_model ?? "—"}</dd>
              <dt className="font-medium text-zinc-500">Max Copies</dt>
              <dd>{caps.copies_max ?? "—"}</dd>
              <dt className="font-medium text-zinc-500">Media Sizes</dt>
              <dd>{caps.media_sizes.join(", ") || "—"}</dd>
              <dt className="font-medium text-zinc-500">Media Sources</dt>
              <dd>{caps.media_sources.join(", ") || "—"}</dd>
              <dt className="font-medium text-zinc-500">Output Bins</dt>
              <dd>{caps.output_bins.join(", ") || "—"}</dd>
            </dl>
            {printer.capabilities_detected_at && (
              <p className="text-xs text-zinc-400">
                Last probed {new Date(printer.capabilities_detected_at).toLocaleString()}
              </p>
            )}
          </div>
        )}
      </Card>
      )}

      {!printer.is_virtual && <SnmpCountersCard printer={printer} onUpdate={setPrinter} />}

      {!printer.is_virtual && (
        <UsageHistoryCard printerId={printer.id} confidence={printer.page_count_confidence} />
      )}
    </div>
  );
}
