"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, listUsers, updateUser, type Role, type UserAccount } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; users: UserAccount[] }
  | { phase: "error"; message: string };

export default function UsersPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [rowError, setRowError] = useState<string | null>(null);

  useEffect(() => {
    if (currentUser === null) {
      router.replace("/login");
    } else if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  function load() {
    listUsers()
      .then((users) => setState({ phase: "ok", users }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load users",
        }),
      );
  }

  useEffect(() => {
    if (currentUser?.role === "admin") load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUser]);

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

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="flex w-full max-w-4xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Users</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Accounts provisioned via Google SSO. New sign-ins default to Viewer unless their email is
          on the initial-admin allowlist — promote/demote here.
        </p>
      </div>

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
              <EmptyState>No one has signed in with Google yet.</EmptyState>
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                  <th className="px-4 py-3 font-medium">Email</th>
                  <th className="px-4 py-3 font-medium">Name</th>
                  <th className="px-4 py-3 font-medium">Role</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Last Login</th>
                </tr>
              </thead>
              <tbody>
                {state.users.map((user) => (
                  <tr
                    key={user.id}
                    className="border-b border-black/[.08] last:border-0 dark:border-white/[.145]"
                  >
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
                      </select>
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
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : "Never"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </div>
  );
}
