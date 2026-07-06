"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  createAttributionAlias,
  deleteAttributionAlias,
  listAttributionAliases,
  listGoogleWorkspaceUsers,
  type AttributionAlias,
  type GoogleWorkspaceUserEntry,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

// The roster can be thousands of entries (see devices/page.tsx's note on why a
// per-row datalist froze the tab), so one shared datalist is used here too.
const ROSTER_DATALIST_ID = "google-workspace-roster-aliases";
const PAGE_SIZE = 50;

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; aliases: AttributionAlias[]; total: number }
  | { phase: "error"; message: string };

export default function AttributionAliasesPage() {
  const [roster, setRoster] = useState<GoogleWorkspaceUserEntry[]>([]);
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const [alias, setAlias] = useState("");
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  function load() {
    listAttributionAliases({ page, pageSize: PAGE_SIZE, search: search || undefined })
      .then((result) => setState({ phase: "ok", aliases: result.items, total: result.total }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load attribution aliases",
        }),
      );
  }

  useEffect(load, [page, search]);

  useEffect(() => {
    listGoogleWorkspaceUsers()
      .then(setRoster)
      .catch(() => setRoster([]));
  }, []);

  function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  }

  async function handleAdd() {
    setSaving(true);
    setFormError(null);
    setLastResult(null);
    try {
      const result = await createAttributionAlias({ alias, resolved_email: email, note: note || null });
      setLastResult(
        result.backfilled_job_count > 0
          ? `Merged — updated ${result.backfilled_job_count} past job${result.backfilled_job_count === 1 ? "" : "s"}.`
          : "Merged.",
      );
      setAlias("");
      setEmail("");
      setNote("");
      load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to add alias");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    setSaving(true);
    try {
      await deleteAttributionAlias(id);
      load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to remove alias");
    } finally {
      setSaving(false);
    }
  }

  const totalPages = state.phase === "ok" ? Math.max(1, Math.ceil(state.total / PAGE_SIZE)) : 1;

  return (
    <Card>
      <CardTitle className="mb-1">Attribution Aliases</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Merge an arbitrary login string — a local computer username (e.g. &quot;matt&quot; instead
        of matt&apos;s real address) or an old/alternate email address — into one staff
        member&apos;s canonical email. Useful for cleanup or after a username change. Google
        Workspace&apos;s own account aliases (shown with a badge below) are merged automatically
        on every sync — this is for anything Google doesn&apos;t already know about.
      </p>

      <form onSubmit={handleSearchSubmit} className="mb-4 flex items-center gap-2">
        <Input
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="Search by alias or resolved email…"
          className="max-w-xs"
        />
        <Button type="submit" variant="secondary">
          Search
        </Button>
        {search && (
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setSearchInput("");
              setSearch("");
              setPage(1);
            }}
          >
            Clear
          </Button>
        )}
      </form>

      {state.phase === "loading" && <Spinner label="Loading…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && (
        <>
          {state.aliases.length > 0 && (
            <table className="mb-2 w-full text-left text-sm">
              <thead>
                <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                  <th className="py-2 font-medium">Alias</th>
                  <th className="py-2 font-medium">Resolves To</th>
                  <th className="py-2 font-medium">Source</th>
                  <th className="py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {state.aliases.map((a) => (
                  <tr key={a.id} className="border-b border-black/[.06] last:border-0 dark:border-white/[.1]">
                    <td className="py-2 font-mono text-xs">{a.alias}</td>
                    <td className="py-2">{a.resolved_email}</td>
                    <td className="py-2">
                      <Badge tone={a.source === "manual" ? "neutral" : "info"}>
                        {a.source === "manual" ? "Manual" : "Google Workspace"}
                      </Badge>
                    </td>
                    <td className="py-2 text-right">
                      <Button
                        variant="danger"
                        className="!px-2 !py-0.5 text-xs"
                        disabled={saving}
                        onClick={() => handleDelete(a.id)}
                      >
                        Remove
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="mb-4 flex items-center justify-between text-xs text-zinc-500">
            <span>
              {state.total.toLocaleString()} alias{state.total === 1 ? "" : "es"}
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

      <div className="flex flex-wrap items-end gap-2">
        <Input value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="matt" className="max-w-[10rem]" />
        <input
          list={ROSTER_DATALIST_ID}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="real-user@domain.com"
          className="rounded border border-black/[.15] bg-transparent px-2 py-1.5 text-sm text-black dark:border-white/[.2] dark:text-zinc-50"
        />
        <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" className="max-w-[10rem]" />
        <Button onClick={handleAdd} disabled={saving || !alias || !email}>
          {saving ? "Merging…" : "Merge"}
        </Button>
      </div>
      {formError && <ErrorState>{formError}</ErrorState>}
      {lastResult && <p className="mt-2 text-xs text-emerald-700 dark:text-emerald-400">{lastResult}</p>}

      <datalist id={ROSTER_DATALIST_ID}>
        {roster.map((u) => (
          <option key={u.email} value={u.email}>
            {u.name ?? u.email}
          </option>
        ))}
      </datalist>
    </Card>
  );
}
