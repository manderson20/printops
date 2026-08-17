"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  createPrinterQuota,
  deletePrinterQuota,
  listGoogleWorkspaceUsers,
  listPrinterQuotas,
  updatePrinter,
  updatePrinterQuota,
  type GoogleWorkspaceUserEntry,
  type Printer,
  type PrinterQuota,
  type QuotaMode,
  type QuotaPeriod,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
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

const MODE_LABELS: Record<QuotaMode, string> = {
  include: "Only these people",
  exclude: "Everyone except these people",
};

const MODE_BLURBS: Record<QuotaMode, string> = {
  include:
    "Only the people listed below have a limit here. Everyone else prints without one.",
  exclude:
    "Everyone has the same limit here, apart from the people listed below, who print without one.",
};

// The roster can be thousands of entries (see devices/page.tsx's note on why
// a per-row datalist froze the tab), so one shared datalist is used here too.
const ROSTER_DATALIST_ID = "printer-quota-roster";

export function QuotasCard({
  printer,
  onUpdate,
}: {
  printer: Printer;
  onUpdate: (printer: Printer) => void;
}) {
  const isAdmin = useCurrentUser()?.role === "admin";
  const mode = printer.quota_mode;

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

  // Non-null while an admin has picked the other mode but not yet confirmed —
  // the switch is spelled out first, since the same rows mean something
  // different afterwards (see the confirmation panel below).
  const [pendingMode, setPendingMode] = useState<QuotaMode | null>(null);
  const [switching, setSwitching] = useState(false);

  function load() {
    listPrinterQuotas(printer.id)
      .then(setQuotas)
      .catch((err: unknown) =>
        setLoadError(
          err instanceof Error ? err.message : "Failed to load quotas",
        ),
      );
  }

  useEffect(load, [printer.id]);
  useEffect(() => {
    listGoogleWorkspaceUsers()
      .then(setRoster)
      .catch(() => setRoster([]));
  }, []);

  const blanket = quotas?.find((quota) => quota.user_email === null) ?? null;
  const userRows = quotas?.filter((quota) => quota.user_email !== null) ?? [];

  async function handleConfirmModeSwitch() {
    if (pendingMode === null) return;
    setSwitching(true);
    setFormError(null);
    try {
      onUpdate(await updatePrinter(printer.id, { quota_mode: pendingMode }));
      setPendingMode(null);
      load();
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : "Failed to change quota mode",
      );
    } finally {
      setSwitching(false);
    }
  }

  // In exclude mode the per-user form only names someone to let out, so it
  // sends no limit at all (the API stores that as an exemption).
  async function handleAdd(asExemption: boolean) {
    setSaving(true);
    setFormError(null);
    try {
      await createPrinterQuota(printer.id, {
        user_email: email.trim() || null,
        period,
        page_limit: asExemption ? null : Number(pageLimit),
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

  async function handleAddBlanket() {
    setSaving(true);
    setFormError(null);
    try {
      await createPrinterQuota(printer.id, {
        user_email: null,
        period,
        page_limit: Number(pageLimit),
      });
      setPageLimit("");
      load();
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : "Failed to set the limit",
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(quotaId: string) {
    setSaving(true);
    try {
      await deletePrinterQuota(printer.id, quotaId);
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
    setEditPageLimit(String(quota.page_limit ?? ""));
  }

  async function handleSaveEdit(quotaId: string) {
    setSaving(true);
    setFormError(null);
    try {
      await updatePrinterQuota(printer.id, quotaId, {
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

  function renderRow(quota: PrinterQuota) {
    // In exclude mode the backend treats the mere *presence* of a per-user
    // row as an exemption, whatever limit it happens to carry (see
    // app/quotas/service.py:get_effective_quota). A row created while the
    // printer was in include mode keeps its old number, so classifying on
    // page_limit alone would show a stale limit, usage ratio and "Over
    // limit" warning for someone nothing is capping.
    const isExemption =
      quota.page_limit === null || (mode === "exclude" && quota.user_email !== null);
    return (
      <tr
        key={quota.id}
        className={`border-b border-black/[.06] last:border-0 dark:border-white/[.1] ${
          quota.active ? "" : "opacity-60"
        }`}
      >
        <td className="py-2 font-mono text-xs">
          {quota.user_email ?? (
            <span className="italic text-zinc-500">Everyone</span>
          )}
          {!quota.active && (
            <Badge tone="warning" className="ml-2 font-sans">
              Not enforced
            </Badge>
          )}
        </td>
        {editingId === quota.id ? (
          <>
            <td className="py-2">
              <select
                value={editPeriod}
                onChange={(e) => setEditPeriod(e.target.value as QuotaPeriod)}
                className="rounded border border-black/[.15] bg-white px-2 py-1 text-xs dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
              >
                {Object.entries(PERIOD_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
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
            <td className="py-2">
              {isExemption ? "—" : PERIOD_LABELS[quota.period]}
            </td>
            <td className="py-2">
              {isExemption ? (
                <span className="text-zinc-500">No limit</span>
              ) : (
                quota.page_limit?.toLocaleString()
              )}
            </td>
            <td className="py-2 text-zinc-600 dark:text-zinc-400">
              {quota.pages_used.toLocaleString()}
              {!isExemption && (
                <>
                  {" / "}
                  {quota.page_limit?.toLocaleString()}
                  {quota.active && quota.pages_used >= (quota.page_limit ?? 0) && (
                    <span className="ml-2 text-red-600 dark:text-red-400">
                      Over limit
                    </span>
                  )}
                </>
              )}
            </td>
            <td className="py-2 text-right">
              {isAdmin && (
                <div className="flex justify-end gap-1">
                  {!isExemption && (
                    <Button
                      variant="secondary"
                      className="!px-2 !py-0.5 text-xs"
                      disabled={saving}
                      onClick={() => startEdit(quota)}
                    >
                      Edit
                    </Button>
                  )}
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
    );
  }

  return (
    <Card>
      <CardTitle className="mb-1">Page Quotas</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Caps how many pages a user can print at this printer over a period you
        choose. A job from someone already at or over their limit is held
        instead of forwarded — an admin has to release it, not the
        submitter&rsquo;s own PIN. Enforcement only actually happens once turned
        on org-wide (Settings &rarr; Quotas).
      </p>

      <div className="mb-4 rounded border border-black/[.08] p-3 dark:border-white/[.145]">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
          Who is limited here
        </div>
        <div className="flex flex-wrap gap-2">
          {(Object.keys(MODE_LABELS) as QuotaMode[]).map((value) => (
            <Button
              key={value}
              variant={mode === value ? "primary" : "secondary"}
              className="!px-3 !py-1 text-xs"
              disabled={!isAdmin || switching || mode === value}
              onClick={() => setPendingMode(value)}
            >
              {MODE_LABELS[value]}
            </Button>
          ))}
        </div>
        <p className="mt-2 text-xs text-zinc-500">{MODE_BLURBS[mode]}</p>

        {pendingMode !== null && pendingMode !== mode && (
          <div className="mt-3 rounded border border-amber-500/40 bg-amber-50 p-3 text-xs dark:bg-amber-950/30">
            <p className="mb-2 font-medium">
              Switch to &ldquo;{MODE_LABELS[pendingMode]}&rdquo;?
            </p>
            <p className="mb-2 text-zinc-700 dark:text-zinc-300">
              {pendingMode === "exclude" ? (
                <>
                  The {userRows.length}{" "}
                  {userRows.length === 1 ? "person" : "people"} listed below
                  currently <strong>have</strong> a limit. Afterwards they will
                  instead be <strong>exempt</strong>, and everyone else will be
                  held to this printer&rsquo;s single limit.
                </>
              ) : (
                <>
                  The {userRows.length}{" "}
                  {userRows.length === 1 ? "person" : "people"} listed below are
                  currently <strong>exempt</strong>. Afterwards they will be the
                  only ones <strong>with</strong> a limit, and everyone else
                  will print without one.
                </>
              )}
            </p>
            <p className="mb-3 text-zinc-500">
              Nothing is deleted — switching back restores exactly what you have
              now.
            </p>
            <div className="flex gap-2">
              <Button
                className="!px-2 !py-0.5 text-xs"
                disabled={switching}
                onClick={handleConfirmModeSwitch}
              >
                {switching ? "Switching…" : "Switch mode"}
              </Button>
              <Button
                variant="secondary"
                className="!px-2 !py-0.5 text-xs"
                disabled={switching}
                onClick={() => setPendingMode(null)}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>

      {quotas === null && !loadError && <Spinner label="Loading quotas…" />}
      {loadError && <ErrorState>{loadError}</ErrorState>}

      {quotas !== null && (
        <>
          {mode === "exclude" && blanket === null && isAdmin && (
            <div className="mb-4 rounded border border-black/[.08] p-3 dark:border-white/[.145]">
              <div className="mb-2 text-xs font-medium">
                Set the limit everyone shares
              </div>
              <div className="flex flex-wrap items-end gap-2">
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
                <Button onClick={handleAddBlanket} disabled={saving || !pageLimit}>
                  {saving ? "Saving…" : "Set limit"}
                </Button>
              </div>
              <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
                Until this is set, nobody is limited here and the exemptions
                below have nothing to exempt anyone from.
              </p>
            </div>
          )}

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
              <tbody>{quotas.map(renderRow)}</tbody>
            </table>
          )}

          {isAdmin && (
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
                {mode === "exclude" ? "Exempt this person" : "User"}
                <input
                  list={ROSTER_DATALIST_ID}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="user@domain.com"
                  className="rounded border border-black/[.15] bg-transparent px-2 py-1.5 text-sm text-black dark:border-white/[.2] dark:text-zinc-50"
                />
              </label>
              {mode === "include" && (
                <>
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
                </>
              )}
              <Button
                onClick={() => handleAdd(mode === "exclude")}
                disabled={
                  saving ||
                  !email.trim() ||
                  (mode === "include" && !pageLimit)
                }
              >
                {saving
                  ? "Adding…"
                  : mode === "exclude"
                    ? "Add exemption"
                    : "Add Quota"}
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
