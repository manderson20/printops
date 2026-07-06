"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  createPrinterQuota,
  deletePrinterQuota,
  listGoogleWorkspaceUsers,
  listPrinterQuotas,
  updatePrinterQuota,
  type GoogleWorkspaceUserEntry,
  type PrinterQuota,
  type QuotaPeriod,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

const PERIOD_LABELS: Record<QuotaPeriod, string> = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
  quarterly: "Quarterly",
  yearly: "Yearly",
};

// The roster can be thousands of entries (see devices/page.tsx's note on why
// a per-row datalist froze the tab), so one shared datalist is used here too.
const ROSTER_DATALIST_ID = "printer-quota-roster";

export function QuotasCard({ printerId }: { printerId: string }) {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [roster, setRoster] = useState<GoogleWorkspaceUserEntry[]>([]);
  const [quotas, setQuotas] = useState<PrinterQuota[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [period, setPeriod] = useState<QuotaPeriod>("monthly");
  const [pageLimit, setPageLimit] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editPeriod, setEditPeriod] = useState<QuotaPeriod>("monthly");
  const [editPageLimit, setEditPageLimit] = useState("");

  function load() {
    listPrinterQuotas(printerId)
      .then(setQuotas)
      .catch((err: unknown) =>
        setLoadError(
          err instanceof Error ? err.message : "Failed to load quotas",
        ),
      );
  }

  useEffect(load, [printerId]);
  useEffect(() => {
    listGoogleWorkspaceUsers()
      .then(setRoster)
      .catch(() => setRoster([]));
  }, []);

  async function handleAdd() {
    setSaving(true);
    setFormError(null);
    try {
      await createPrinterQuota(printerId, {
        user_email: email.trim() || null,
        period,
        page_limit: Number(pageLimit),
      });
      setEmail("");
      setPageLimit("");
      load();
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : "Failed to add quota",
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(quotaId: string) {
    setSaving(true);
    try {
      await deletePrinterQuota(printerId, quotaId);
      load();
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : "Failed to remove quota",
      );
    } finally {
      setSaving(false);
    }
  }

  function startEdit(quota: PrinterQuota) {
    setEditingId(quota.id);
    setEditPeriod(quota.period);
    setEditPageLimit(String(quota.page_limit));
  }

  async function handleSaveEdit(quotaId: string) {
    setSaving(true);
    setFormError(null);
    try {
      await updatePrinterQuota(printerId, quotaId, {
        period: editPeriod,
        page_limit: Number(editPageLimit),
      });
      setEditingId(null);
      load();
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : "Failed to update quota",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-1">Page Quotas</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Caps how many pages a user can print at this printer over a period you
        choose. A job from someone already at or over their limit is held
        instead of forwarded — an admin has to release it, not the
        submitter&rsquo;s own PIN. Leave the user blank for a default limit that
        applies to anyone without their own row. Enforcement only actually
        happens once turned on org-wide (Settings &rarr; Quotas).
      </p>

      {quotas === null && !loadError && <Spinner label="Loading quotas…" />}
      {loadError && <ErrorState>{loadError}</ErrorState>}

      {quotas !== null && (
        <>
          {quotas.length > 0 && (
            <table className="mb-4 w-full text-left text-sm">
              <thead>
                <tr className="border-b border-black/[.08] text-xs uppercase tracking-wide text-zinc-500 dark:border-white/[.145]">
                  <th className="py-2 font-medium">User</th>
                  <th className="py-2 font-medium">Period</th>
                  <th className="py-2 font-medium">Limit</th>
                  <th className="py-2 font-medium">Used this period</th>
                  <th className="py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {quotas.map((quota) => (
                  <tr
                    key={quota.id}
                    className="border-b border-black/[.06] last:border-0 dark:border-white/[.1]"
                  >
                    <td className="py-2 font-mono text-xs">
                      {quota.user_email ?? (
                        <span className="italic text-zinc-500">
                          Default (anyone)
                        </span>
                      )}
                    </td>
                    {editingId === quota.id ? (
                      <>
                        <td className="py-2">
                          <select
                            value={editPeriod}
                            onChange={(e) =>
                              setEditPeriod(e.target.value as QuotaPeriod)
                            }
                            className="rounded border border-black/[.15] bg-white px-2 py-1 text-xs dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
                          >
                            {Object.entries(PERIOD_LABELS).map(
                              ([value, label]) => (
                                <option key={value} value={value}>
                                  {label}
                                </option>
                              ),
                            )}
                          </select>
                        </td>
                        <td className="py-2">
                          <Input
                            type="number"
                            min="1"
                            value={editPageLimit}
                            onChange={(e) => setEditPageLimit(e.target.value)}
                            className="w-20 !py-1 text-xs"
                          />
                        </td>
                        <td className="py-2 text-zinc-600 dark:text-zinc-400">
                          {quota.pages_used.toLocaleString()}
                        </td>
                        <td className="py-2 text-right">
                          <div className="flex justify-end gap-1">
                            <Button
                              className="!px-2 !py-0.5 text-xs"
                              disabled={saving}
                              onClick={() => handleSaveEdit(quota.id)}
                            >
                              Save
                            </Button>
                            <Button
                              variant="secondary"
                              className="!px-2 !py-0.5 text-xs"
                              disabled={saving}
                              onClick={() => setEditingId(null)}
                            >
                              Cancel
                            </Button>
                          </div>
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="py-2">{PERIOD_LABELS[quota.period]}</td>
                        <td className="py-2">
                          {quota.page_limit.toLocaleString()}
                        </td>
                        <td className="py-2 text-zinc-600 dark:text-zinc-400">
                          {quota.pages_used.toLocaleString()} /{" "}
                          {quota.page_limit.toLocaleString()}
                          {quota.pages_used >= quota.page_limit && (
                            <span className="ml-2 text-red-600 dark:text-red-400">
                              Over limit
                            </span>
                          )}
                        </td>
                        <td className="py-2 text-right">
                          {isAdmin && (
                            <div className="flex justify-end gap-1">
                              <Button
                                variant="secondary"
                                className="!px-2 !py-0.5 text-xs"
                                disabled={saving}
                                onClick={() => startEdit(quota)}
                              >
                                Edit
                              </Button>
                              <Button
                                variant="danger"
                                className="!px-2 !py-0.5 text-xs"
                                disabled={saving}
                                onClick={() => handleDelete(quota.id)}
                              >
                                Remove
                              </Button>
                            </div>
                          )}
                        </td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {isAdmin && (
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
                User (blank = default)
                <input
                  list={ROSTER_DATALIST_ID}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="user@domain.com"
                  className="rounded border border-black/[.15] bg-transparent px-2 py-1.5 text-sm text-black dark:border-white/[.2] dark:text-zinc-50"
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
                Period
                <select
                  value={period}
                  onChange={(e) => setPeriod(e.target.value as QuotaPeriod)}
                  className="rounded border border-black/[.15] bg-white px-2 py-1.5 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
                >
                  {Object.entries(PERIOD_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
                Page limit
                <Input
                  type="number"
                  min="1"
                  value={pageLimit}
                  onChange={(e) => setPageLimit(e.target.value)}
                  className="max-w-[8rem]"
                />
              </label>
              <Button onClick={handleAdd} disabled={saving || !pageLimit}>
                {saving ? "Adding…" : "Add Quota"}
              </Button>
            </div>
          )}

          {formError && <ErrorState>{formError}</ErrorState>}

          <datalist id={ROSTER_DATALIST_ID}>
            {roster.map((u) => (
              <option key={u.email} value={u.email}>
                {u.name ?? u.email}
              </option>
            ))}
          </datalist>
        </>
      )}
    </Card>
  );
}
