"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ApiError,
  checkPrinterStatus,
  deletePrinter,
  getMdmConnection,
  getPrinter,
  listJobs,
  purgePrinterJobs,
  rediscoverPrinter,
  resyncQueue,
  updatePrinter,
  type Job,
  type MdmConnectionInfo,
  type Printer,
} from "@/lib/api";
import { capabilityBadges } from "@/lib/capabilities";
import { formatBytes, formatRelativeTime } from "@/lib/format";
import { attributionMethodInfo, jobStatusInfo } from "@/lib/jobStatus";
import { printerStatusInfo } from "@/lib/printerStatus";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { PrintReleaseCard } from "./PrintRelease";
import { SnmpCountersCard } from "./SnmpCounters";
import { TonerCartridgesCard } from "./TonerCartridges";
import { UsageHistoryCard } from "./UsageHistory";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; printer: Printer }
  | { phase: "error"; message: string };

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

export default function PrinterDetailPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const isAdmin = useCurrentUser()?.role === "admin";
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [form, setForm] = useState<Record<string, string>>({});
  const [airprintEnabled, setAirprintEnabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rediscovering, setRediscovering] = useState(false);
  const [resyncing, setResyncing] = useState(false);
  const [checkingStatus, setCheckingStatus] = useState(false);
  const [purging, setPurging] = useState(false);
  const [purgeResult, setPurgeResult] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [connection, setConnection] = useState<MdmConnectionInfo | null>(null);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  useEffect(() => {
    getPrinter(params.id)
      .then((printer) => {
        setState({ phase: "ok", printer });
        setForm(
          Object.fromEntries(
            EDITABLE_FIELDS.map(([field]) => [field, (printer as never)[field] ?? ""]),
          ),
        );
        setAirprintEnabled(printer.airprint_enabled);
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load printer",
        }),
      );
    listJobs({ printer_id: params.id, limit: 5 })
      .then(setJobs)
      .catch(() => setJobs([]));
    getMdmConnection(params.id)
      .then(setConnection)
      .catch(() => setConnection(null));
  }, [params.id]);

  async function handleCopy(field: string, value: string) {
    await navigator.clipboard.writeText(value);
    setCopiedField(field);
    setTimeout(() => setCopiedField((current) => (current === field ? null : current)), 1500);
  }

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      const printer = await updatePrinter(params.id, { ...form, airprint_enabled: airprintEnabled });
      setState({ phase: "ok", printer });
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
      const printer = await rediscoverPrinter(params.id);
      setState({ phase: "ok", printer });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Rediscovery failed");
    } finally {
      setRediscovering(false);
    }
  }

  async function handleResync() {
    setResyncing(true);
    setActionError(null);
    try {
      const printer = await resyncQueue(params.id);
      setState({ phase: "ok", printer });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Queue resync failed");
    } finally {
      setResyncing(false);
    }
  }

  async function handleDelete() {
    if (!confirm("Delete this printer?")) return;
    await deletePrinter(params.id);
    router.push("/printers");
  }

  async function handleCheckStatus() {
    setCheckingStatus(true);
    setActionError(null);
    try {
      const printer = await checkPrinterStatus(params.id);
      setState({ phase: "ok", printer });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Status check failed");
    } finally {
      setCheckingStatus(false);
    }
  }

  async function handlePurgeQueue() {
    if (!confirm("Cancel every job queued on this printer? This can't be undone.")) return;
    setPurging(true);
    setActionError(null);
    setPurgeResult(null);
    try {
      const result = await purgePrinterJobs(params.id);
      setPurgeResult(
        result.cancelled_count === 0
          ? "No PrintOps-tracked jobs were pending — the CUPS queue has been cleared."
          : `Cancelled ${result.cancelled_count} pending job${result.cancelled_count === 1 ? "" : "s"}.`,
      );
      const refreshed = await listJobs({ printer_id: params.id, limit: 5 }).catch(() => null);
      if (refreshed) setJobs(refreshed);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Queue purge failed");
    } finally {
      setPurging(false);
    }
  }

  if (state.phase === "loading") {
    return <Spinner label="Loading printer…" />;
  }
  if (state.phase === "error") {
    return <ErrorState>{state.message}</ErrorState>;
  }

  const { printer } = state;
  const caps = printer.capabilities;

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">{printer.name}</h1>
        {isAdmin && (
          <Button variant="danger" onClick={handleDelete}>
            Delete
          </Button>
        )}
      </div>

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

      <Card>
        <CardTitle className="mb-4">Details</CardTitle>
        <div className="grid grid-cols-2 gap-4">
          {EDITABLE_FIELDS.map(([field, label]) => (
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
              configured (e.g. via MDM) can print to it. Recommended off for printers
              handling confidential documents.
            </span>
          </span>
        </label>

        {actionError && <ErrorState>{actionError}</ErrorState>}
        {isAdmin && (
          <Button onClick={handleSave} disabled={saving} className="mt-4">
            {saving ? "Saving…" : "Save"}
          </Button>
        )}
      </Card>

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
          This points at the PrintOps server, not the printer itself — clients print through
          the proxy.
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
                <Button variant="secondary" className="!px-3 !py-1 text-xs" onClick={() => handleCopy(label, value)}>
                  {copiedField === label ? "Copied" : "Copy"}
                </Button>
              </div>
            ))}
          </div>
        )}
      </Card>

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
              Common cause: IPP is disabled on the device by default (true for most Canon,
              and many other, printers) — enable it in the printer&apos;s own admin page, then
              click Rediscover below.
            </p>
          </div>
        )}

        {!caps && !printer.capabilities_error && (
          <EmptyState>No capabilities detected yet.</EmptyState>
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

      <TonerCartridgesCard printerId={printer.id} colorSupported={!!caps?.color_supported} />

      <PrintReleaseCard
        printer={printer}
        onUpdate={(updated) => setState({ phase: "ok", printer: updated })}
      />

      <SnmpCountersCard
        printer={printer}
        onUpdate={(updated) => setState({ phase: "ok", printer: updated })}
      />

      <UsageHistoryCard printerId={printer.id} confidence={printer.page_count_confidence} />

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <CardTitle>Recent Jobs</CardTitle>
          <div className="flex items-center gap-3">
            {isAdmin && (
              <Button
                variant="danger"
                className="!px-3 !py-1 text-xs"
                onClick={handlePurgeQueue}
                disabled={purging}
              >
                {purging ? "Purging…" : "Purge Queue"}
              </Button>
            )}
            <Link
              href={`/jobs?printer_id=${printer.id}`}
              className="text-xs font-medium text-accent hover:underline"
            >
              View all
            </Link>
          </div>
        </div>

        {purgeResult && (
          <p className="mb-3 text-xs text-emerald-700 dark:text-emerald-400">{purgeResult}</p>
        )}

        {jobs === null && <Spinner label="Loading jobs…" />}
        {jobs !== null && jobs.length === 0 && (
          <EmptyState>No jobs logged for this printer yet.</EmptyState>
        )}
        {jobs !== null && jobs.length > 0 && (
          <div className="flex flex-col gap-2 text-sm">
            {jobs.map((job) => {
              const info = jobStatusInfo(job.status);
              const attribution = attributionMethodInfo(job.attribution_method);
              return (
                <div
                  key={job.id}
                  className="flex items-center justify-between border-t border-black/[.08] pt-2 first:border-t-0 first:pt-0 dark:border-white/[.1]"
                >
                  <div className="flex flex-col">
                    <span className="flex items-center gap-2 text-zinc-700 dark:text-zinc-300">
                      {job.submitted_by ?? "Unknown user"}
                      <Badge tone={attribution.tone}>{attribution.label}</Badge>
                    </span>
                    <span className="text-xs text-zinc-400">
                      {new Date(job.created_at).toLocaleString()} · {formatBytes(job.file_size_bytes)}
                    </span>
                  </div>
                  <Badge tone={info.tone}>{info.label}</Badge>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
