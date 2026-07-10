"use client";

import { Fragment, useEffect, useState } from "react";
import { ApiError, listUsers, updateUser, type Role, type UserAccount } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Input, Textarea } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

const PAGE_SIZE = 50;

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; users: UserAccount[]; total: number }
  | { phase: "error"; message: string };

export default function UsersSettingsPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [rowError, setRowError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [editingOuFor, setEditingOuFor] = useState<string | null>(null);
  const [ouDraft, setOuDraft] = useState("");

  function load() {
    listUsers({ page, pageSize: PAGE_SIZE, search: search || undefined })
      .then((result) => setState({ phase: "ok", users: result.items, total: result.total }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load users",
        }),
      );
  }

  useEffect(load, [page, search]);

  function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  }

  async function handleRoleChange(user: UserAccount, role: Role) {
    setRowError(null);
    try {
      await updateUser(user.id, { role });
      load();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to update role");
    }
  }

  function startEditingOus(user: UserAccount) {
    setEditingOuFor(user.id);
    setOuDraft((user.granted_ou_paths ?? []).join("\n"));
  }

  async function handleSaveOus(user: UserAccount) {
    setRowError(null);
    const granted_ou_paths = ouDraft
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    try {
      await updateUser(user.id, { granted_ou_paths });
      setEditingOuFor(null);
      load();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to update granted OUs");
    }
  }

  async function handleActiveToggle(user: UserAccount) {
    setRowError(null);
    try {
      await updateUser(user.id, { is_active: !user.is_active });
      load();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to update account");
    }
  }

  async function handleExemptToggle(user: UserAccount) {
    setRowError(null);
    try {
      await updateUser(user.id, { exempt_from_timeout: !user.exempt_from_timeout });
      load();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to update account");
    }
  }

  const totalPages = state.phase === "ok" ? Math.max(1, Math.ceil(state.total / PAGE_SIZE)) : 1;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold text-black dark:text-zinc-50">Users</h2>
        <p className="mt-1 text-sm text-zinc-500">
          Accounts provisioned via Google SSO. New sign-ins default to Viewer unless their email is
          on the initial-admin allowlist — promote/demote here.
        </p>
      </div>

      <form onSubmit={handleSearchSubmit} className="flex items-center gap-2">
        <Input
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="Search by name or email…"
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

      {state.phase === "loading" && <Spinner label="Loading users…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && (
        <Card className="p-0">
          {rowError && (
            <div className="border-b border-black/[.08] p-4 dark:border-white/[.145]">
              <ErrorState>{rowError}</ErrorState>
            </div>
          )}
          {state.users.length === 0 ? (
            <div className="p-6">
              <EmptyState>
                {search ? "No accounts match that search." : "No one has signed in with Google yet."}
              </EmptyState>
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                  <th className="px-4 py-3 font-medium">Email</th>
                  <th className="px-4 py-3 font-medium">Name</th>
                  <th className="px-4 py-3 font-medium">Role</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Timeout</th>
                  <th className="px-4 py-3 font-medium">Last Login</th>
                </tr>
              </thead>
              <tbody>
                {state.users.map((user) => (
                  <Fragment key={user.id}>
                    <tr className="border-b border-black/[.08] last:border-0 dark:border-white/[.145]">
                      <td className="px-4 py-3 text-black dark:text-zinc-50">{user.email}</td>
                      <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">{user.name ?? "—"}</td>
                      <td className="px-4 py-3">
                        <select
                          value={user.role}
                          onChange={(e) => handleRoleChange(user, e.target.value as Role)}
                          className="rounded-lg border border-black/[.15] bg-white px-2 py-1 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
                        >
                          <option value="admin">Admin</option>
                          <option value="viewer">Viewer</option>
                          <option value="ou_viewer">OU Viewer</option>
                        </select>
                        {user.role === "ou_viewer" && editingOuFor !== user.id && (
                          <button
                            onClick={() => startEditingOus(user)}
                            className="ml-2 text-xs text-accent underline"
                          >
                            {user.granted_ou_paths && user.granted_ou_paths.length > 0
                              ? `${user.granted_ou_paths.length} OU${user.granted_ou_paths.length === 1 ? "" : "s"}`
                              : "Set OUs…"}
                          </button>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <button onClick={() => handleActiveToggle(user)}>
                          {user.is_active ? (
                            <Badge tone="success">Active</Badge>
                          ) : (
                            <Badge tone="neutral">Deactivated</Badge>
                          )}
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        <button onClick={() => handleExemptToggle(user)} title="Toggle idle-timeout exemption">
                          {user.exempt_from_timeout ? (
                            <Badge tone="info">No timeout</Badge>
                          ) : (
                            <Badge tone="neutral">Normal</Badge>
                          )}
                        </button>
                      </td>
                      <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                        {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : "Never"}
                      </td>
                    </tr>
                    {editingOuFor === user.id && (
                      <tr className="border-b border-black/[.08] bg-black/[.02] last:border-0 dark:border-white/[.145] dark:bg-white/[.03]">
                        <td colSpan={6} className="px-4 py-3">
                          <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
                            Granted OU paths for {user.email} — one per line (e.g. /Schools/Elementary/BuildingA).
                            Nested sub-OUs are included automatically. Empty means this account sees no data yet.
                            <Textarea
                              value={ouDraft}
                              onChange={(e) => setOuDraft(e.target.value)}
                              rows={3}
                              placeholder="/Schools/Elementary/BuildingA"
                              className="max-w-md font-mono text-xs"
                            />
                          </label>
                          <div className="mt-2 flex gap-2">
                            <Button onClick={() => handleSaveOus(user)}>Save</Button>
                            <Button variant="secondary" onClick={() => setEditingOuFor(null)}>
                              Cancel
                            </Button>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}

          <div className="flex items-center justify-between border-t border-black/[.08] px-4 py-3 text-sm text-zinc-500 dark:border-white/[.145]">
            <span>
              {state.total.toLocaleString()} account{state.total === 1 ? "" : "s"}
              {totalPages > 1 && ` — page ${page} of ${totalPages}`}
            </span>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                Next
              </Button>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
