"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ApiError,
  listPrinters,
  testPrintPrinter,
  type Printer,
} from "@/lib/api";
import { capabilityBadges } from "@/lib/capabilities";
import { formatRelativeTime } from "@/lib/format";
import {
  hasNetworkWarning,
  hasWaitingJobsWarning,
  printerStatusInfo,
} from "@/lib/printerStatus";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

// Every field an admin might plausibly search a printer by — name, network
// identity, and hardware identity — flattened into one lowercased haystack
// per printer. capabilities.make_model is the device's own self-reported
// string (e.g. "MINOLTA bizhub C361i"), often more complete/accurate than
// the stored manufacturer/model pair, so it's included too rather than
// relying on just one or the other.
function searchHaystack(printer: Printer): string {
  return [
    printer.name,
    printer.ip_address,
    printer.hostname,
    printer.manufacturer,
    printer.model,
    printer.capabilities?.make_model,
    printer.serial_number,
    printer.building,
    printer.room,
    printer.department,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

const CSV_COLUMNS: { header: string; value: (printer: Printer) => string }[] = [
  { header: "Name", value: (p) => p.name },
  { header: "Status", value: (p) => printerStatusInfo(p.status).label },
  { header: "Manufacturer", value: (p) => p.manufacturer ?? "" },
  { header: "Model", value: (p) => p.model ?? "" },
  { header: "Serial Number", value: (p) => p.serial_number ?? "" },
  { header: "IP Address", value: (p) => p.ip_address ?? "" },
  { header: "Hostname", value: (p) => p.hostname ?? "" },
  { header: "Building", value: (p) => p.building ?? "" },
  { header: "Room", value: (p) => p.room ?? "" },
  { header: "Department", value: (p) => p.department ?? "" },
  { header: "Page Count", value: (p) => p.page_count_total?.toString() ?? "" },
  {
    header: "AirPrint",
    value: (p) => (p.airprint_enabled ? "Discoverable" : "Hidden"),
  },
  { header: "Archived", value: (p) => (p.archived_at ? "Yes" : "No") },
];

// Quote any field containing a comma, quote, or newline — the minimal
// escaping CSV needs, per RFC 4180. Excel/Sheets/Numbers all read this.
function csvField(value: string): string {
  if (/[",\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

// Client-side, not a backend endpoint — same rationale as the search box
// above: the full (filtered) list is already sitting in memory, so there's
// nothing an API round-trip would add except latency.
function downloadPrintersCsv(printers: Printer[]) {
  const lines = [
    CSV_COLUMNS.map((c) => csvField(c.header)).join(","),
    ...printers.map((printer) =>
      CSV_COLUMNS.map((c) => csvField(c.value(printer))).join(","),
    ),
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `printops-printers-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; printers: Printer[] }
  | { phase: "error"; message: string };

type TestPrintState =
  | { phase: "sending" }
  // The API's message is carried through rather than replaced with a fixed
  // one: on a printer that holds jobs for release it says the page is waiting
  // and what releases it, which is the difference between "sent" and "sent,
  // and no paper is coming until you act".
  | { phase: "ok"; message: string }
  | { phase: "error"; message: string };

export default function PrintersPage() {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [testPrints, setTestPrints] = useState<Record<string, TestPrintState>>(
    {},
  );
  // Ten columns did not fit, so the table scrolled sideways and the things an
  // admin acts on — Test Print above all — sat off the right-hand edge. The
  // row now carries only what gets scanned down the list (is this the printer
  // I mean, is it healthy, what is its address, where is it, how much has it
  // done); everything you want after picking a row opens underneath it.
  const [expandedRows, setExpandedRows] = useState<Record<string, boolean>>({});
  const [showArchived, setShowArchived] = useState(false);
  const [search, setSearch] = useState("");

  useEffect(() => {
    listPrinters({ includeArchived: showArchived })
      .then((printers) => setState({ phase: "ok", printers }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message:
            error instanceof Error ? error.message : "Failed to load printers",
        }),
      );

    // The API polls each printer's status every 60s in the background
    // (app/main.py); re-fetch periodically here so the list reflects that
    // without a manual reload. Silent — doesn't reset to the loading phase,
    // so it never interrupts whatever the admin is doing on this page.
    const interval = setInterval(() => {
      listPrinters({ includeArchived: showArchived })
        .then((printers) => setState({ phase: "ok", printers }))
        .catch(() => {});
    }, 30_000);
    return () => clearInterval(interval);
  }, [showArchived]);

  // The API appends its explanation after an em dash when the page is held,
  // and returns `lp`'s bare "request id is ..." line when it is not.
  function isHeldMessage(message: string) {
    return (
      message.includes("being held") ||
      message.includes("holds jobs for release")
    );
  }

  async function handleTestPrint(printerId: string) {
    setTestPrints((prev) => ({ ...prev, [printerId]: { phase: "sending" } }));
    try {
      const result = await testPrintPrinter(printerId);
      setTestPrints((prev) => ({
        ...prev,
        [printerId]: { phase: "ok", message: result.message },
      }));
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

  // Client-side, not server-side — listPrinters has no search/pagination
  // params to begin with (the full fleet is small enough to load at once),
  // so filtering the already-loaded list is simplest and needs no API
  // change. Every search word must appear somewhere in the haystack (AND,
  // not OR) so e.g. "hp central" narrows rather than broadens.
  const filteredPrinters = useMemo(() => {
    if (state.phase !== "ok") return [];
    const words = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (words.length === 0) return state.printers;
    return state.printers.filter((printer) => {
      const haystack = searchHaystack(printer);
      return words.every((word) => haystack.includes(word));
    });
  }, [state, search]);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">
            Printers
          </h1>
          <WikiHelpLink page="Printers" />
        </div>
        <div className="flex items-center gap-4">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name, IP, model, brand…"
            className="w-64"
          />
          <label className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            Show archived
          </label>
          <Button
            variant="secondary"
            disabled={filteredPrinters.length === 0}
            onClick={() => downloadPrintersCsv(filteredPrinters)}
          >
            Export CSV
          </Button>
          {isAdmin && (
            <Link href="/printers/new-follow-me">
              <Button variant="secondary">Add Follow-Me Queue</Button>
            </Link>
          )}
          {isAdmin && (
            <Link href="/printers/new">
              <Button>Add Printer</Button>
            </Link>
          )}
        </div>
      </div>

      {state.phase === "loading" && <Spinner label="Loading printers…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}
      {state.phase === "ok" && state.printers.length === 0 && (
        <EmptyState>No printers yet. Add one to get started.</EmptyState>
      )}
      {state.phase === "ok" &&
        state.printers.length > 0 &&
        filteredPrinters.length === 0 && (
          <EmptyState>No printers match &quot;{search}&quot;.</EmptyState>
        )}
      {state.phase === "ok" && filteredPrinters.length > 0 && (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-black/[.03] text-zinc-600 dark:bg-white/[.05] dark:text-zinc-400">
                <tr>
                  <th className="w-8 px-2 py-3" />
                  <th className="px-4 py-3 font-medium">Name</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">IP Address</th>
                  <th className="px-4 py-3 font-medium">Location</th>
                  <th className="px-4 py-3 font-medium">Pages</th>
                </tr>
              </thead>
              <tbody>
                {filteredPrinters.map((printer) => {
                  const testPrint = testPrints[printer.id];
                  const open = expandedRows[printer.id] ?? false;
                  const detailId = `printer-detail-${printer.id}`;
                  return (
                    <Fragment key={printer.id}>
                      <tr className="border-t border-black/[.08] hover:bg-black/[.02] dark:border-white/[.1] dark:hover:bg-white/[.03]">
                        <td className="px-2 py-3 align-top">
                          {/* Its own control rather than a click on the row:
                              the row already contains a link to the printer,
                              and a row that both navigates and expands is a
                              coin toss for whoever clicks it. */}
                          <button
                            type="button"
                            aria-expanded={open}
                            aria-controls={detailId}
                            aria-label={
                              open
                                ? `Hide details for ${printer.name}`
                                : `Show details for ${printer.name}`
                            }
                            onClick={() =>
                              setExpandedRows((prev) => ({
                                ...prev,
                                [printer.id]: !open,
                              }))
                            }
                            className="flex h-6 w-6 items-center justify-center rounded text-zinc-500 hover:bg-black/[.05] hover:text-black dark:hover:bg-white/[.08] dark:hover:text-zinc-50"
                          >
                            <span
                              className={`transition-transform ${open ? "rotate-90" : ""}`}
                              aria-hidden="true"
                            >
                              &#9656;
                            </span>
                          </button>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <Link
                              href={`/printers/${printer.id}`}
                              className="font-medium text-black hover:underline dark:text-zinc-50"
                            >
                              {printer.name}
                            </Link>
                            {printer.is_virtual && (
                              <Badge tone="neutral">Follow-Me Queue</Badge>
                            )}
                            {printer.archived_at && (
                              <Badge tone="neutral">Archived</Badge>
                            )}
                          </div>
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
                                <div className="flex flex-wrap items-center gap-1">
                                  <Badge tone={info.tone} title={title || undefined}>
                                    {info.label}
                                  </Badge>
                                  {/* Both of these stay in the collapsed row on
                                      purpose: they are the reason someone opens
                                      this page at all, and a warning you have to
                                      expand a row to find is a warning nobody
                                      sees. */}
                                  {hasWaitingJobsWarning(printer.status_reasons) && (
                                    <Badge
                                      tone="danger"
                                      title={
                                        printer.status_message ??
                                        "Jobs are waiting for this printer"
                                      }
                                    >
                                      Jobs waiting
                                    </Badge>
                                  )}
                                  {hasNetworkWarning(printer.status_reasons) && (
                                    <Badge
                                      tone="warning"
                                      title={
                                        printer.status_message ??
                                        "This printer is missing status checks"
                                      }
                                    >
                                      Network
                                    </Badge>
                                  )}
                                  {printer.queue_sync_error && (
                                    <Badge tone="danger" title={printer.queue_sync_error}>
                                      Sync Failed
                                    </Badge>
                                  )}
                                </div>
                                <span className="text-xs text-zinc-400">
                                  {formatRelativeTime(printer.status_checked_at)}
                                </span>
                              </div>
                            );
                          })()}
                        </td>
                        <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                          {printer.ip_address ?? "—"}
                        </td>
                        <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                          {[printer.building, printer.room, printer.department]
                            .filter(Boolean)
                            .join(" / ") || "—"}
                        </td>
                        <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                          {printer.page_count_total ?? "—"}
                        </td>
                      </tr>

                      {open && (
                        <tr
                          id={detailId}
                          className="border-t border-black/[.08] bg-black/[.02] dark:border-white/[.1] dark:bg-white/[.03]"
                        >
                          <td />
                          <td colSpan={5} className="px-4 py-4">
                            <div className="flex flex-col gap-4">
                              <dl className="grid grid-cols-2 gap-x-8 gap-y-3 sm:grid-cols-3">
                                <div>
                                  <dt className="text-xs text-zinc-500">Model</dt>
                                  <dd className="text-zinc-700 dark:text-zinc-300">
                                    {printer.manufacturer ?? ""} {printer.model ?? "—"}
                                    {printer.capabilities_error && (
                                      <span className="ml-2 text-amber-700 dark:text-amber-400">
                                        (capabilities not detected)
                                      </span>
                                    )}
                                  </dd>
                                </div>
                                <div>
                                  <dt className="text-xs text-zinc-500">Queue</dt>
                                  <dd>
                                    {printer.queue_sync_error ? (
                                      <Badge tone="danger" title={printer.queue_sync_error}>
                                        Sync Failed
                                      </Badge>
                                    ) : (
                                      <Badge tone="success">Synced</Badge>
                                    )}
                                  </dd>
                                </div>
                                <div>
                                  <dt className="text-xs text-zinc-500">AirPrint</dt>
                                  <dd>
                                    {printer.airprint_enabled ? (
                                      <Badge tone="success">Discoverable</Badge>
                                    ) : (
                                      <Badge tone="neutral">Hidden</Badge>
                                    )}
                                  </dd>
                                </div>
                              </dl>

                              <div>
                                <p className="mb-1 text-xs text-zinc-500">Capabilities</p>
                                {(() => {
                                  const badges = capabilityBadges(printer.capabilities);
                                  if (badges.length === 0) {
                                    return <span className="text-xs text-zinc-400">—</span>;
                                  }
                                  // Shown in full — there is room for a dozen
                                  // finishing options down here, which is why
                                  // the old "+N more" is gone.
                                  return (
                                    <div className="flex flex-wrap items-center gap-1">
                                      {badges.map((badge) => (
                                        <Badge key={badge} tone="info">
                                          {badge}
                                        </Badge>
                                      ))}
                                    </div>
                                  );
                                })()}
                              </div>

                              {isAdmin && (
                                <div className="flex flex-wrap items-center gap-3">
                                  <Button
                                    variant="secondary"
                                    className="!px-3 !py-1 text-xs"
                                    disabled={testPrint?.phase === "sending"}
                                    onClick={() => handleTestPrint(printer.id)}
                                  >
                                    {testPrint?.phase === "sending"
                                      ? "Sending…"
                                      : "Test Print"}
                                  </Button>
                                  {testPrint?.phase === "ok" &&
                                    (isHeldMessage(testPrint.message) ? (
                                      // Amber, not green: the submission
                                      // succeeded, but nothing is going to
                                      // print until someone acts.
                                      <span className="text-xs text-amber-700 dark:text-amber-400">
                                        {testPrint.message}
                                      </span>
                                    ) : (
                                      <span className="text-xs text-emerald-700 dark:text-emerald-400">
                                        Sent — check Jobs
                                      </span>
                                    ))}
                                  {testPrint?.phase === "error" && (
                                    <span className="text-xs text-red-600 dark:text-red-400">
                                      {testPrint.message}
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
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
