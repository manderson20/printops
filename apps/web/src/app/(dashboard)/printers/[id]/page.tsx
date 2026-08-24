"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  checkPrinterStatus,
  confirmPrinterRedirect,
  dismissPrinterRedirect,
  getCupsQueueDefaults,
  rediscoverPrinter,
  updatePrinter,
} from "@/lib/api";
import { capabilityBadges } from "@/lib/capabilities";
import { formatRelativeTime } from "@/lib/format";
import {
  NETWORK_UNSTABLE_REASON,
  UNREACHABLE_WITH_JOBS_REASON,
  printerStatusInfo,
} from "@/lib/printerStatus";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { usePrinterDetail } from "./PrinterDetailContext";
import { RollMediaCard } from "./RollMedia";
import { SnmpCountersCard } from "./SnmpCounters";
import { UsageHistoryCard } from "./UsageHistory";

// Connection settings, kept separate from the identity fields below because
// getting one of them wrong stops the printer working rather than just
// mislabelling it. They were previously not editable here at all: when the
// LCACTC Kyocera was switched to TLS-only IPP (2026-08-20) it needed port 443
// and /ipp/print, and the form offered only the TLS checkbox — which on its own
// cannot work, since the device serves cleartext on 631 and a TLS handshake
// there just fails. There was no way to fix the printer from this page.
const CONNECTION_FIELDS = [
  ["port", "Port"],
  ["ipp_path", "IPP Path"],
] as const;

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

// On-demand comparison against the CUPS-generated queue's own PPD default —
// separate from the stored capabilities fetch since it shells out on the
// server (see app/printers/cups_ppd_info.py). A mismatch against the
// device's own default_media_size is the same signal that identified the
// earlier *DefaultColorModel bug.
function CupsQueueDefaultCheck({
  printerId,
  deviceDefault,
}: {
  printerId: string;
  deviceDefault: string | null;
}) {
  const [pageSize, setPageSize] = useState<string | null | undefined>(
    undefined,
  );

  useEffect(() => {
    getCupsQueueDefaults(printerId)
      .then((result) => setPageSize(result.page_size))
      .catch(() => setPageSize(null));
  }, [printerId]);

  if (pageSize === undefined) {
    return (
      <p className="text-xs text-zinc-400">Checking CUPS queue default…</p>
    );
  }
  if (pageSize === null) {
    return (
      <p className="text-xs text-zinc-400">
        CUPS queue default: not available (queue may not be synced yet).
      </p>
    );
  }

  // deviceDefault is a raw PWG name (e.g. "na_letter_8.5x11in"); the CUPS
  // PPD choice is a plain PPD label (e.g. "Letter") — an exact string match
  // isn't meaningful, so flag only an unambiguous case: the PPD default
  // looks nothing like the device's reported default at all.
  const mismatch =
    deviceDefault !== null &&
    !deviceDefault
      .toLowerCase()
      .includes(pageSize.toLowerCase().replace(/[^a-z0-9]/g, ""));

  return (
    <div
      className={
        mismatch
          ? "rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
          : "text-xs text-zinc-400"
      }
    >
      CUPS queue default: <span className="font-mono">{pageSize}</span>
      {mismatch && (
        <p className="mt-1">
          This doesn&apos;t look like it matches the device&apos;s own reported
          default ({deviceDefault}) — the CUPS-generated queue may be overriding
          it.
        </p>
      )}
    </div>
  );
}

export default function PrinterOverviewTab() {
  const { printer, setPrinter } = usePrinterDetail();
  const isAdmin = useCurrentUser()?.role === "admin";
  const editableFields = printer.is_virtual
    ? VIRTUAL_EDITABLE_FIELDS
    : EDITABLE_FIELDS;
  const connectionFields = printer.is_virtual ? [] : CONNECTION_FIELDS;
  const [form, setForm] = useState<Record<string, string>>(
    Object.fromEntries(
      [...editableFields, ...connectionFields].map((entry) => [
        entry[0],
        String((printer as never)[entry[0]] ?? ""),
      ]),
    ),
  );
  const [airprintEnabled, setAirprintEnabled] = useState(
    printer.airprint_enabled,
  );
  const [useTls, setUseTls] = useState(printer.use_tls);
  const [saving, setSaving] = useState(false);
  const [rediscovering, setRediscovering] = useState(false);
  const [checkingStatus, setCheckingStatus] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [redirectBusy, setRedirectBusy] = useState(false);

  const caps = printer.capabilities;

  // A device that redirects to a *different host* is claiming to be somewhere
  // else entirely. PrintOps records that rather than acting on it, because
  // moving a printer means every job after it — including held ones — goes to
  // the new machine. This is where a person decides.
  async function handleRedirect(accept: boolean) {
    setRedirectBusy(true);
    setActionError(null);
    try {
      const updated = accept
        ? await confirmPrinterRedirect(printer.id)
        : await dismissPrinterRedirect(printer.id);
      setPrinter(updated);
      // The edit form was initialised from the printer as it was. Left alone,
      // the next Save would submit the *old* address over the move that was
      // just confirmed and rebuild the CUPS queue against it — undoing the
      // move without anyone touching the connection fields.
      // Rebuilt exactly the way the form is initialised, so the two cannot
      // drift as fields are added.
      setForm(
        Object.fromEntries(
          [...editableFields, ...connectionFields].map((entry) => [
            entry[0],
            String((updated as never)[entry[0]] ?? ""),
          ]),
        ),
      );
      setUseTls(updated.use_tls);
      setAirprintEnabled(updated.airprint_enabled);
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "Failed to update this printer",
      );
    } finally {
      setRedirectBusy(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      const { port, ...rest } = form;
      const portGiven =
        !printer.is_virtual && port !== undefined && port !== "";
      const portNumber = Number(port);
      // Number("abc") is NaN, which serialises to null and 422s the whole save
      // with a generic failure message — so anything that isn't a real port is
      // named here instead, while the other edits stay on screen.
      if (
        portGiven &&
        (!Number.isInteger(portNumber) || portNumber < 1 || portNumber > 65535)
      ) {
        setActionError("Port must be a whole number between 1 and 65535.");
        return;
      }
      const updated = await updatePrinter(printer.id, {
        ...rest,
        // The form holds every value as a string; `port` is an integer on the
        // API and a string 422s the entire save, taking the other edits with it.
        ...(portGiven ? { port: portNumber } : {}),
        airprint_enabled: airprintEnabled,
        use_tls: useTls,
      });
      setPrinter(updated);
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "Failed to save changes",
      );
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
      setActionError(
        err instanceof ApiError ? err.message : "Rediscovery failed",
      );
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
      setActionError(
        err instanceof ApiError ? err.message : "Status check failed",
      );
    } finally {
      setCheckingStatus(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {printer.is_virtual && (
        <Card>
          <p className="text-sm text-zinc-500">
            This is a virtual Follow-Me queue — it has no real device behind it,
            so there&apos;s no status, capabilities, or usage counters to show
            here. Jobs sent to it are always held and released at whichever real
            printer the person walks up to.
          </p>
        </Card>
      )}

      {!printer.is_virtual && (
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <CardTitle>Status</CardTitle>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                onClick={handleCheckStatus}
                disabled={checkingStatus}
              >
                {checkingStatus ? "Checking…" : "Check Now"}
              </Button>
            </div>
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
                {printer.pending_redirect && (
                  <div className="rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/40">
                    <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
                      This printer says it has moved to{" "}
                      {printer.pending_redirect.tls ? "ipps" : "ipp"}://
                      {printer.pending_redirect.host}:
                      {printer.pending_redirect.port}
                      {printer.pending_redirect.path}
                    </p>
                    <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
                      A printer that answers on a different port or over TLS is
                      moved automatically — that is the same machine, reached
                      differently. This names a different address entirely,
                      which PrintOps will not act on by itself: every job sent
                      here afterwards, including any waiting for release, would
                      go to whatever is at that address. Confirm only if you
                      know this printer was moved or replaced. The new address
                      is checked before anything changes.
                    </p>
                    <div className="mt-2 flex gap-2">
                      <Button
                        className="!px-3 !py-1 text-xs"
                        disabled={redirectBusy}
                        onClick={() => handleRedirect(true)}
                      >
                        {redirectBusy ? "Checking…" : "Confirm move"}
                      </Button>
                      <Button
                        variant="secondary"
                        className="!px-3 !py-1 text-xs"
                        disabled={redirectBusy}
                        onClick={() => handleRedirect(false)}
                      >
                        Dismiss
                      </Button>
                    </div>
                  </div>
                )}
                {printer.status_message && (
                  <p className="text-zinc-600 dark:text-zinc-400">
                    {printer.status_message}
                  </p>
                )}
                {printer.status_reasons &&
                  printer.status_reasons.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {printer.status_reasons.map((reason) => (
                        // Amber, not red, for the network warning: the printer
                        // is working and it is the path to it that isn't, so a
                        // red badge would misname the fault and send someone to
                        // the wrong machine.
                        <Badge
                          key={reason}
                          tone={
                            reason === NETWORK_UNSTABLE_REASON
                              ? "warning"
                              : "danger"
                          }
                        >
                          {reason === NETWORK_UNSTABLE_REASON
                            ? "Network unstable"
                            : reason === UNREACHABLE_WITH_JOBS_REASON
                              ? "Jobs waiting"
                              : reason}
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
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, [field]: e.target.value }))
                }
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
              Off = hidden from automatic discovery on Macs/iPads; only devices
              explicitly configured (e.g. via MDM) can print to it. Recommended
              off for printers handling confidential documents.
            </span>
          </span>
        </label>

        {!printer.is_virtual && (
          <div className="mt-4 grid grid-cols-2 gap-4">
            {CONNECTION_FIELDS.map(([field, label]) => (
              <Field key={field} label={label}>
                <Input
                  value={form[field] ?? ""}
                  disabled={!isAdmin}
                  inputMode={field === "port" ? "numeric" : undefined}
                  // An empty IPP Path means "detect it", so the box shows what
                  // detection last found as its placeholder. Placeholder text
                  // renders grey and a real value renders solid, which is the
                  // whole distinction: grey = the server worked this out and
                  // will keep it current, solid = someone pinned it and
                  // detection will not touch it.
                  placeholder={
                    field === "port"
                      ? "631"
                      : (printer.ipp_path_detected ?? "/ipp/print")
                  }
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, [field]: e.target.value }))
                  }
                />
              </Field>
            ))}
          </div>
        )}

        {!printer.is_virtual && (
          <p className="mt-2 text-xs text-zinc-500">
            {printer.ipp_path
              ? "IPP Path is set manually, so automatic detection won't change it. Clear the box to hand it back to detection."
              : printer.ipp_path_detected
                ? `IPP Path is detected automatically — currently ${printer.ipp_path_detected}. Type a value only to override it.`
                : "Leave IPP Path empty and PrintOps will work it out from the printer."}{" "}
            Port 631 is plain IPP; a printer set to require TLS normally serves
            IPPS on 443 instead. Turning on TLS below without also changing the
            port will fail — the device is still speaking cleartext on 631, so
            the TLS handshake never completes.
          </p>
        )}

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
                Encrypts traffic between PrintOps and this printer. Most office
                printers use a self-signed certificate, so this isn&apos;t
                strong endpoint verification, and not every device handles IPPS
                cleanly — only turn on if you&apos;ve confirmed this printer
                supports it.
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
              <Button
                variant="secondary"
                onClick={handleRediscover}
                disabled={rediscovering}
              >
                {rediscovering ? "Probing…" : "Rediscover"}
              </Button>
            )}
          </div>

          {printer.capabilities_error && (
            <div className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
              <p className="font-medium">
                Printer saved, but capabilities couldn&apos;t be detected.
              </p>
              <p className="mt-1 text-amber-800 dark:text-amber-300">
                {printer.capabilities_error}
              </p>
              <p className="mt-1 text-amber-800 dark:text-amber-300">
                Common cause: IPP is disabled on the device by default (true for
                most Canon, and many other, printers) — enable it in the
                printer&apos;s own admin page, then click Rediscover below.
              </p>
            </div>
          )}

          {!caps && !printer.capabilities_error && (
            <EmptyState>No capabilities detected yet.</EmptyState>
          )}

          {caps?.tls_supported && !printer.use_tls && (
            <div className="mb-3 rounded border border-blue-300 bg-blue-50 p-3 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200">
              This printer advertises IPPS (TLS) support but it&apos;s not
              turned on — enable &quot;Connect via TLS (IPPS)&quot; above to
              encrypt traffic to it.
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
                <dt className="font-medium text-zinc-500">Default Page Size</dt>
                <dd>{caps.default_media_size ?? "—"}</dd>
                <dt className="font-medium text-zinc-500">Media Sizes</dt>
                <dd>{caps.media_sizes.join(", ") || "—"}</dd>
                <dt className="font-medium text-zinc-500">Media Sources</dt>
                <dd>{caps.media_sources.join(", ") || "—"}</dd>
                <dt className="font-medium text-zinc-500">Output Bins</dt>
                <dd>{caps.output_bins.join(", ") || "—"}</dd>
              </dl>

              {caps.media_trays.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-medium text-zinc-500">
                    Paper Trays
                  </p>
                  <ul className="flex flex-col gap-1">
                    {caps.media_trays.map((tray, i) => (
                      <li
                        key={i}
                        className="text-xs text-zinc-600 dark:text-zinc-400"
                      >
                        {tray.source ?? "Tray"}:{" "}
                        {tray.width_in && tray.height_in
                          ? `${tray.width_in} × ${tray.height_in} in`
                          : "—"}
                        {tray.type ? ` (${tray.type})` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {isAdmin && (
                <CupsQueueDefaultCheck
                  printerId={printer.id}
                  deviceDefault={caps.default_media_size}
                />
              )}

              {printer.capabilities_detected_at && (
                <p className="text-xs text-zinc-400">
                  Last probed{" "}
                  {new Date(printer.capabilities_detected_at).toLocaleString()}
                </p>
              )}
            </div>
          )}
        </Card>
      )}

      {!printer.is_virtual && (
        <RollMediaCard printer={printer} onUpdate={setPrinter} />
      )}

      {!printer.is_virtual && (
        <SnmpCountersCard printer={printer} onUpdate={setPrinter} />
      )}

      {!printer.is_virtual && (
        <UsageHistoryCard
          printerId={printer.id}
          confidence={printer.page_count_confidence}
        />
      )}
    </div>
  );
}
