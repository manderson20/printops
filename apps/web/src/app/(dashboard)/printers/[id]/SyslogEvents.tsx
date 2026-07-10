"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, listPrinterSyslogEvents, type SyslogEvent, type SyslogSeverity } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { Badge } from "@/components/ui/Badge";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

const RECENT_COUNT = 10;

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

export function SyslogEventsCard({ printerId }: { printerId: string }) {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    listPrinterSyslogEvents(printerId, { pageSize: RECENT_COUNT })
      .then((result) => setState({ phase: "ok", events: result.items, total: result.total }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof ApiError ? error.message : "Failed to load syslog events",
        }),
      );
  }, [printerId]);

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <CardTitle>Syslog</CardTitle>
        {state.phase === "ok" && state.total > 0 && (
          <Link
            href={`/syslog?printer_id=${printerId}`}
            className="text-xs font-medium text-accent hover:underline"
          >
            View all ({state.total.toLocaleString()})
          </Link>
        )}
      </div>
      <p className="mb-4 text-xs text-zinc-500">
        Messages this printer has sent to the syslog collector (infra/syslog-relay), if it&apos;s
        been configured (on the printer&apos;s own admin UI) to send them here. Off by default —
        see Settings → Syslog.
      </p>

      {state.phase === "loading" && <Spinner label="Loading…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}
      {state.phase === "ok" && state.events.length === 0 && (
        <EmptyState>
          No syslog events received from this printer yet.
        </EmptyState>
      )}
      {state.phase === "ok" && state.events.length > 0 && (
        <div className="flex flex-col gap-2">
          {state.events.map((event) => (
            <div
              key={event.id}
              className="rounded-lg border border-black/[.08] p-3 text-sm dark:border-white/[.1]"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  {severityBadge(event.severity)}
                  {event.app_name && (
                    <span className="text-xs font-medium text-zinc-500">{event.app_name}</span>
                  )}
                </div>
                <span className="text-xs text-zinc-400">
                  {formatRelativeTime(event.received_at)}
                </span>
              </div>
              <button
                type="button"
                onClick={() => setExpanded(expanded === event.id ? null : event.id)}
                className="mt-1 block w-full text-left text-zinc-700 hover:underline dark:text-zinc-300"
              >
                {event.message}
              </button>
              {expanded === event.id && (
                <pre className="mt-2 whitespace-pre-wrap break-all rounded bg-black/[.03] p-2 text-xs text-zinc-500 dark:bg-white/[.05]">
                  {event.raw}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
