"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ApiError,
  listAuditActions,
  listAuditEvents,
  type AuditChange,
  type AuditEvent,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

const PAGE_SIZE = 50;

const SELECT_CLASS =
  "rounded-lg border border-black/[.15] bg-white px-2 py-1 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; events: AuditEvent[]; total: number }
  | { phase: "error"; message: string };

/** The top level of the dotted action name — "settings", "printer", "auth".
 *  Actions are hierarchical so the filter can offer the group without a
 *  separate category column existing in the table. */
function actionGroups(actions: string[]): string[] {
  return [...new Set(actions.map((a) => a.split(".")[0]))].sort();
}

function toneFor(action: string) {
  if (action.startsWith("auth.login.failed")) return "warning" as const;
  // Deletions and credential rotations are the rows somebody scanning this
  // page is most likely looking for.
  if (action.endsWith(".delete") || action.includes("regenerate")) return "danger" as const;
  if (action.startsWith("auth")) return "info" as const;
  return "neutral" as const;
}

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function Changes({ changes }: { changes: Record<string, AuditChange> }) {
  return (
    <table className="mt-2 w-full table-fixed text-xs">
      <tbody>
        {Object.entries(changes).map(([field, change]) => (
          <tr key={field} className="align-top">
            <td className="w-48 truncate py-0.5 pr-3 font-medium text-zinc-500">{field}</td>
            <td className="py-0.5">
              <span className="text-zinc-500 line-through">{shown(change?.from)}</span>
              <span className="px-2 text-zinc-400">→</span>
              <span className="font-medium">{shown(change?.to)}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AuditList() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const actionPrefix = searchParams.get("action_prefix") ?? "";

  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [actions, setActions] = useState<string[]>([]);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [actor, setActor] = useState("");
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    listAuditActions()
      .then((data) => setActions(data.actions))
      // A filter that cannot be populated is not worth an error state — the
      // list below is the page, and it loads independently.
      .catch(() => setActions([]));
  }, []);

  // Reset to "loading" during render rather than inside the effect, the same
  // idiom @/lib/useCurrentUser and the Insights pages use: setting state
  // synchronously in an effect body cascades a second render, and doing it here
  // also stops the previous filter's rows showing through under the new
  // heading for a frame or two.
  const queryKey = `${actionPrefix}|${actor}|${search}|${page}`;
  const [loadedKey, setLoadedKey] = useState(queryKey);
  if (queryKey !== loadedKey) {
    setLoadedKey(queryKey);
    setState({ phase: "loading" });
  }

  useEffect(() => {
    let cancelled = false;
    listAuditEvents({
      actionPrefix: actionPrefix || undefined,
      actor: actor || undefined,
      search: search || undefined,
      page,
      pageSize: PAGE_SIZE,
    })
      .then((data) => {
        if (!cancelled) setState({ phase: "ok", events: data.events, total: data.total });
      })
      .catch((error: ApiError | Error) => {
        if (!cancelled) setState({ phase: "error", message: error.message });
      });
    return () => {
      cancelled = true;
    };
  }, [actionPrefix, actor, search, page]);

  function setPrefix(value: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set("action_prefix", value);
    else params.delete("action_prefix");
    setPage(1);
    router.replace(`/audit${params.toString() ? `?${params}` : ""}`);
  }

  const totalPages =
    state.phase === "ok" ? Math.max(1, Math.ceil(state.total / PAGE_SIZE)) : 1;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Audit log</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Every configuration change, account change and sign-in, with who made it. Each
          entry is written in the same transaction as the change it describes, so nothing
          here can describe something that did not happen.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <select
          className={SELECT_CLASS}
          value={actionPrefix}
          onChange={(event) => setPrefix(event.target.value)}
        >
          <option value="">All activity</option>
          {actionGroups(actions).map((group) => (
            <option key={group} value={group}>
              {group}
            </option>
          ))}
        </select>
        <Input
          placeholder="Who (email)"
          value={actor}
          onChange={(event) => {
            setActor(event.target.value);
            setPage(1);
          }}
          className="w-48"
        />
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setSearch(searchInput.trim());
            setPage(1);
          }}
        >
          <Input
            placeholder="Search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            className="w-56"
          />
          <Button type="submit" variant="secondary">
            Search
          </Button>
        </form>
        {state.phase === "ok" ? (
          <span className="text-sm text-zinc-500">
            {state.total} {state.total === 1 ? "entry" : "entries"}
          </span>
        ) : null}
      </div>

      {state.phase === "loading" ? <Spinner /> : null}
      {state.phase === "error" ? (
        <ErrorState>Couldn&apos;t load the audit log: {state.message}</ErrorState>
      ) : null}

      {state.phase === "ok" && state.events.length === 0 ? (
        <EmptyState>
          Nothing matches. The log starts from when auditing was added, so an older
          change will not be here.
        </EmptyState>
      ) : null}

      {state.phase === "ok" && state.events.length > 0 ? (
        <Card className="divide-y divide-black/[.06] p-0 dark:divide-white/[.08]">
          {state.events.map((event) => {
            const hasDetail = event.changes && Object.keys(event.changes).length > 0;
            const isOpen = expanded === event.id;
            return (
              <div key={event.id} className="px-4 py-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <Badge tone={toneFor(event.action)}>{event.action}</Badge>
                    <span className="text-sm">{event.summary}</span>
                  </div>
                  <span className="text-xs tabular-nums text-zinc-500">
                    {new Date(event.occurred_at).toLocaleString()}
                  </span>
                </div>

                <div className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-zinc-500">
                  <span>{event.actor_email}</span>
                  {event.impersonated_by ? (
                    <span className="text-amber-700 dark:text-amber-400">
                      acting as this account — really {event.impersonated_by}
                    </span>
                  ) : null}
                  {event.source_ip ? <span>from {event.source_ip}</span> : null}
                  {hasDetail ? (
                    <button
                      type="button"
                      className="underline underline-offset-2 hover:text-zinc-700 dark:hover:text-zinc-300"
                      onClick={() => setExpanded(isOpen ? null : event.id)}
                    >
                      {isOpen ? "Hide what changed" : "What changed"}
                    </button>
                  ) : null}
                </div>

                {isOpen && event.changes ? <Changes changes={event.changes} /> : null}
              </div>
            );
          })}
        </Card>
      ) : null}

      {state.phase === "ok" && totalPages > 1 ? (
        <div className="flex items-center gap-3">
          <Button
            variant="secondary"
            disabled={page <= 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >
            Previous
          </Button>
          <span className="text-sm text-zinc-500">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="secondary"
            disabled={page >= totalPages}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
          </Button>
        </div>
      ) : null}
    </div>
  );
}

export default function AuditPage() {
  // useSearchParams needs a Suspense boundary in the App Router, same as the
  // syslog page.
  return (
    <Suspense fallback={<Spinner />}>
      <AuditList />
    </Suspense>
  );
}
