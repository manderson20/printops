"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  createUser,
  listGoogleWorkspaceOrgUnits,
  listGoogleWorkspaceUsers,
  listUsers,
  updateUser,
  type GoogleWorkspaceUserEntry,
  type Role,
  type UserAccount,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

const PAGE_SIZE = 50;

// Shared across every row's email <input>, not one per row — the synced
// roster can be thousands of entries (see devices/page.tsx's
// ROSTER_DATALIST_ID, which hit this same scaling issue first), and a
// per-row copy would mean rendering roster_size * row_count <option>
// elements.
const ROSTER_DATALIST_ID = "google-workspace-roster-users";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; users: UserAccount[]; total: number }
  | { phase: "error"; message: string };

// Google Workspace OU paths are slash-delimited, e.g.
// "/Schools/Elementary/BuildingA" — indenting by depth in the picker below
// makes the hierarchy visually obvious without needing a real tree widget.
function ouDepth(path: string): number {
  return Math.max(0, path.split("/").filter(Boolean).length - 1);
}

function ouLabel(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

export default function UsersSettingsPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [rowError, setRowError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const [addingUser, setAddingUser] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newRole, setNewRole] = useState<Role>("viewer");
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [roster, setRoster] = useState<GoogleWorkspaceUserEntry[]>([]);

  const [orgUnits, setOrgUnits] = useState<string[] | null>(null);
  const [orgUnitsError, setOrgUnitsError] = useState<string | null>(null);
  const [editingOuFor, setEditingOuFor] = useState<string | null>(null);
  const [ouFilter, setOuFilter] = useState("");
  const [draftPaths, setDraftPaths] = useState<Set<string>>(new Set());
  const [savingOus, setSavingOus] = useState(false);

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

  useEffect(() => {
    listGoogleWorkspaceUsers()
      .then(setRoster)
      .catch(() => setRoster([]));
    listGoogleWorkspaceOrgUnits()
      .then(setOrgUnits)
      .catch((error: unknown) =>
        setOrgUnitsError(error instanceof Error ? error.message : "Failed to load org units"),
      );
  }, []);

  const filteredOrgUnits = useMemo(() => {
    if (!orgUnits) return [];
    const needle = ouFilter.trim().toLowerCase();
    if (!needle) return orgUnits;
    return orgUnits.filter((path) => path.toLowerCase().includes(needle));
  }, [orgUnits, ouFilter]);

  function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  }

  async function handleAddUser(e: React.FormEvent) {
    e.preventDefault();
    setAddError(null);
    setAdding(true);
    try {
      await createUser({ email: newEmail.trim(), role: newRole });
      setNewEmail("");
      setNewRole("viewer");
      setAddingUser(false);
      setPage(1);
      load();
    } catch (err) {
      setAddError(err instanceof ApiError ? err.message : "Failed to add user");
    } finally {
      setAdding(false);
    }
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

  function startEditingOus(user: UserAccount) {
    setEditingOuFor(user.id);
    setOuFilter("");
    setDraftPaths(new Set(user.granted_ou_paths ?? []));
  }

  function toggleDraftPath(path: string) {
    setDraftPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  async function handleSaveOus(user: UserAccount) {
    setRowError(null);
    setSavingOus(true);
    try {
      await updateUser(user.id, { granted_ou_paths: [...draftPaths] });
      setEditingOuFor(null);
      load();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : "Failed to update granted OUs");
    } finally {
      setSavingOus(false);
    }
  }

  const totalPages = state.phase === "ok" ? Math.max(1, Math.ceil(state.total / PAGE_SIZE)) : 1;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-black dark:text-zinc-50">Users &amp; Permissions</h2>
            <WikiHelpLink page="Settings-Users-and-Permissions" />
          </div>
          <p className="mt-1 text-sm text-zinc-500">
            Accounts provisioned via Google SSO. New sign-ins default to Viewer unless their email
            is on the initial-admin allowlist, or you&apos;ve pre-added them below. Promote/demote
            here — for &quot;OU Viewer&quot; accounts, also grant which org units they can see in
            Insights, picked from your synced directory.
          </p>
        </div>
        <Button variant="secondary" onClick={() => setAddingUser((v) => !v)}>
          {addingUser ? "Cancel" : "Add User"}
        </Button>
      </div>

      {addingUser && (
        <Card>
          <form onSubmit={handleAddUser} className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
              Email
              <Input
                type="email"
                required
                list={ROSTER_DATALIST_ID}
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                placeholder="Start typing a name or email…"
                className="w-72"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
              Role
              <select
                value={newRole}
                onChange={(e) => setNewRole(e.target.value as Role)}
                className="rounded-lg border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
              >
                <option value="admin">Admin</option>
                <option value="viewer">Viewer</option>
                <option value="ou_viewer">OU Viewer</option>
              </select>
            </label>
            <Button type="submit" disabled={adding}>
              {adding ? "Adding…" : "Add"}
            </Button>
          </form>
          <p className="mt-2 text-xs text-zinc-500">
            Matches against your synced Google Workspace directory as you type. Pre-provisions the
            account by email — it takes effect the moment they sign in with Google for the first
            time (or immediately, if they&apos;ve already signed in before).
          </p>
          {addError && (
            <div className="mt-2">
              <ErrorState>{addError}</ErrorState>
            </div>
          )}
        </Card>
      )}

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
                            className="ml-2 text-xs text-accent hover:underline"
                          >
                            {user.granted_ou_paths && user.granted_ou_paths.length > 0
                              ? `${user.granted_ou_paths.length} OU${user.granted_ou_paths.length === 1 ? "" : "s"} granted`
                              : "Grant OUs…"}
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
                          <p className="mb-2 text-sm text-zinc-700 dark:text-zinc-300">
                            Granted org units for <strong>{user.email}</strong> — nested sub-OUs are
                            included automatically. None selected means this account sees no data yet.
                          </p>
                          {orgUnitsError && <ErrorState>{orgUnitsError}</ErrorState>}
                          {!orgUnitsError && orgUnits === null && <Spinner label="Loading org units…" />}
                          {orgUnits !== null && (
                            <>
                              <Input
                                value={ouFilter}
                                onChange={(e) => setOuFilter(e.target.value)}
                                placeholder="Filter org units…"
                                className="mb-2 w-full max-w-sm"
                              />
                              <div className="max-h-64 max-w-lg overflow-y-auto rounded-lg border border-black/[.08] dark:border-white/[.1]">
                                {filteredOrgUnits.length === 0 ? (
                                  <p className="p-3 text-sm text-zinc-500">No org units match.</p>
                                ) : (
                                  filteredOrgUnits.map((path) => (
                                    <label
                                      key={path}
                                      className="flex items-center gap-2 border-b border-black/[.05] px-3 py-1.5 text-sm last:border-0 hover:bg-black/[.02] dark:border-white/[.06] dark:hover:bg-white/[.03]"
                                      style={{ paddingLeft: `${0.75 + ouDepth(path) * 1}rem` }}
                                    >
                                      <input
                                        type="checkbox"
                                        checked={draftPaths.has(path)}
                                        onChange={() => toggleDraftPath(path)}
                                      />
                                      <span className="text-zinc-700 dark:text-zinc-300">
                                        {ouLabel(path)}
                                      </span>
                                      <span className="truncate text-xs text-zinc-400">{path}</span>
                                    </label>
                                  ))
                                )}
                              </div>
                            </>
                          )}
                          <div className="mt-3 flex gap-2">
                            <Button onClick={() => handleSaveOus(user)} disabled={savingOus}>
                              {savingOus ? "Saving…" : "Save"}
                            </Button>
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

      <datalist id={ROSTER_DATALIST_ID}>
        {roster.map((u) => (
          <option key={u.email} value={u.email}>
            {u.name ?? u.email}
          </option>
        ))}
      </datalist>
    </div>
  );
}
