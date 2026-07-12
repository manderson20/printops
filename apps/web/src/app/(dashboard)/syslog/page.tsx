"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ApiError,
  listPrinters,
  listSyslogEvents,
  type Printer,
  type SyslogEvent,
  type SyslogSeverity,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

const PAGE_SIZE = 50;

const SELECT_CLASS =
  "rounded-lg border border-black/[.15] bg-white px-2 py-1 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50";

const SEVERITIES: SyslogSeverity[] = [
  "emerg",
  "alert",
  "crit",
  "err",
  "warning",
  "notice",
  "info",
  "debug",
];

function severityBadge(severity: SyslogSeverity | null) {
  if (severity === "emerg" || severity === "alert" || severity === "crit" || severity === "err") {
    return <Badge tone="danger">{severity}</Badge>;
  }
  if (severity === "warning") return <Badge tone="warning">warning</Badge>;
  if (severity === "notice" || severity === "info") return <Badge tone="info">{severity}</Badge>;
  if (severity === "debug") return <Badge tone="neutral">debug</Badge>;
  return <Badge tone="neutral">unknown</Badge>;
}

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; events: SyslogEvent[]; total: number }
  | { phase: "error"; message: string };

function SyslogList() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const printerId = searchParams.get("printer_id") ?? "";

  const [printers, setPrinters] = useState<Printer[]>([]);
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [severity, setSeverity] = useState<SyslogSeverity | "">("");
  const [unmatchedOnly, setUnmatchedOnly] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    listPrinters({ includeArchived: true })
      .then(setPrinters)
      .catch(() => setPrinters([]));
  }, []);

  useEffect(() => {
    listSyslogEvents({
      printerId: printerId || undefined,
      severity: severity || undefined,
      unmatchedOnly,
      search: search || undefined,
      page,
      pageSize: PAGE_SIZE,
    })
      .then((result) => setState({ phase: "ok", events: result.items, total: result.total }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof ApiError ? error.message : "Failed to load syslog events",
        }),
      );
  }, [printerId, severity, unmatchedOnly, search, page]);

  function handlePrinterFilterChange(value: string) {
    const params = new URLSearchParams(searchParams);
    if (value) {
      params.set("printer_id", value);
    } else {
      params.delete("printer_id");
    }
    setPage(1);
    const qs = params.toString();
    router.push(qs ? `/syslog?${qs}` : "/syslog");
  }

  function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  }

  const totalPages = state.phase === "ok" ? Math.max(1, Math.ceil(state.total / PAGE_SIZE)) : 1;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Syslog</h1>
          <WikiHelpLink page="Syslog-Viewer" />
        </div>
      </div>
      <p className="text-sm text-zinc-500">
        Messages printers have sent to the syslog collector, when configured (on the printer&apos;s
        own admin UI) to send them here — see Settings → Syslog. Off by default.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
          Printer
          <select
            value={printerId}
            onChange={(e) => handlePrinterFilterChange(e.target.value)}
            className={SELECT_CLASS}
          >
            <option value="">All printers</option>
            {printers.map((printer) => (
              <option key={printer.id} value={printer.id}>
                {printer.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
          Severity
          <select
            value={severity}
            onChange={(e) => {
              setPage(1);
              setSeverity(e.target.value as SyslogSeverity | "");
            }}
            className={SELECT_CLASS}
          >
            <option value="">All severities</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
          <input
            type="checkbox"
            checked={unmatchedOnly}
            onChange={(e) => {
              setPage(1);
              setUnmatchedOnly(e.target.checked);
            }}
          />
          Unmatched only
        </label>
        <form onSubmit={handleSearchSubmit} className="flex items-center gap-2">
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search message text…"
            className="max-w-xs"
          />
          <Button type="submit" variant="secondary">
            Search
          </Button>
        </form>
        {printerId && (
          <button
            onClick={() => handlePrinterFilterChange("")}
            className="text-sm font-medium text-accent hover:underline"
          >
            Clear printer filter
          </button>
        )}
      </div>

      {state.phase === "loading" && <Spinner label="Loading syslog events…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}
      {state.phase === "ok" && state.events.length === 0 && (
        <EmptyState>No syslog events match these filters.</EmptyState>
      )}
      {state.phase === "ok" && state.events.length > 0 && (
        <>
          <Card className="overflow-hidden p-0">
            <table className="w-full text-left text-sm">
              <thead className="bg-black/[.03] text-zinc-600 dark:bg-white/[.05] dark:text-zinc-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Time</th>
                  <th className="px-4 py-3 font-medium">Printer</th>
                  <th className="px-4 py-3 font-medium">Severity</th>
                  <th className="px-4 py-3 font-medium">Source</th>
                  <th className="px-4 py-3 font-medium">Message</th>
                </tr>
              </thead>
              <tbody>
                {state.events.map((event) => (
                  <tr
                    key={event.id}
                    className="cursor-pointer border-t border-black/[.08] align-top hover:bg-black/[.02] dark:border-white/[.1] dark:hover:bg-white/[.03]"
                    onClick={() => setExpanded(expanded === event.id ? null : event.id)}
                  >
                    <td className="whitespace-nowrap px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {new Date(event.received_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3">
                      {event.printer_id ? (
                        <Link
                          href={`/printers/${event.printer_id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="font-medium text-black hover:underline dark:text-zinc-50"
                        >
                          {event.printer_name ?? event.printer_id}
                        </Link>
                      ) : (
                        <Badge tone="neutral">Unmatched</Badge>
                      )}
                    </td>
                    <td className="px-4 py-3">{severityBadge(event.severity)}</td>
                    <td className="px-4 py-3 font-mono text-xs text-zinc-500">{event.source_ip}</td>
                    <td className="px-4 py-3 text-zinc-700 dark:text-zinc-300">
                      {event.message}
                      {expanded === event.id && (
                        <pre className="mt-2 max-w-2xl whitespace-pre-wrap break-all rounded bg-black/[.03] p-2 text-xs text-zinc-500 dark:bg-white/[.05]">
                          {event.raw}
                        </pre>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <div className="flex items-center justify-between text-xs text-zinc-500">
            <span>
              {state.total.toLocaleString()} event{state.total === 1 ? "" : "s"}
              {totalPages > 1 && ` — page ${page} of ${totalPages}`}
            </span>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                className="!px-2 !py-1 text-xs"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                className="!px-2 !py-1 text-xs"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default function SyslogPage() {
  return (
    <Suspense fallback={<Spinner label="Loading syslog events…" />}>
      <SyslogList />
    </Suspense>
  );
}
