"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  createPrinterAllowedOu,
  deletePrinterAllowedOu,
  listAllGoogleWorkspaceOrgUnits,
  listPrinterAllowedOus,
  type PrinterAllowedOu,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";

export function SelfServiceAccessCard({ printerId }: { printerId: string }) {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [allowed, setAllowed] = useState<PrinterAllowedOu[] | null>(null);
  const [orgUnits, setOrgUnits] = useState<string[]>([]);
  const [selectedOu, setSelectedOu] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function load() {
    listPrinterAllowedOus(printerId)
      .then(setAllowed)
      .catch(() => setAllowed([]));
  }

  useEffect(load, [printerId]);
  useEffect(() => {
    if (!isAdmin) return;
    listAllGoogleWorkspaceOrgUnits()
      .then((paths) => {
        setOrgUnits(paths);
        if (paths.length > 0) setSelectedOu(paths[0]);
      })
      .catch(() => setOrgUnits([]));
  }, [isAdmin]);

  if (!isAdmin) return null;

  async function handleAdd() {
    if (!selectedOu) return;
    setSaving(true);
    setError(null);
    try {
      await createPrinterAllowedOu(printerId, selectedOu);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add org unit");
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove(allowedId: string) {
    setSaving(true);
    setError(null);
    try {
      await deletePrinterAllowedOu(printerId, allowedId);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove org unit");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-3">Self-Service Print Access</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Restricts who can pick this printer on the self-service Print page (the &quot;Print&quot;
        nav item), by Google Workspace org unit. With none listed below, every logged-in user
        can pick this printer — unrelated to normal AirPrint/MDM printing, which this never
        restricts.
      </p>

      {allowed !== null && allowed.length > 0 && (
        <ul className="mb-3 flex flex-col gap-1">
          {allowed.map((row) => (
            <li
              key={row.id}
              className="flex items-center justify-between rounded border border-black/[.08] px-2 py-1 text-xs dark:border-white/[.1]"
            >
              <span className="font-mono">{row.ou_path}</span>
              <Button
                variant="danger"
                className="!px-2 !py-0.5 text-xs"
                disabled={saving}
                onClick={() => handleRemove(row.id)}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}

      {orgUnits.length > 0 ? (
        <div className="flex items-end gap-2">
          <select
            value={selectedOu}
            onChange={(e) => setSelectedOu(e.target.value)}
            className="flex-1 rounded border border-black/[.15] bg-transparent px-2 py-1.5 text-sm text-black dark:border-white/[.2] dark:text-zinc-50"
          >
            {orgUnits.map((path) => (
              <option key={path} value={path}>
                {path}
              </option>
            ))}
          </select>
          <Button
            variant="secondary"
            className="!px-3 !py-1 text-xs"
            onClick={handleAdd}
            disabled={saving}
          >
            {saving ? "Adding…" : "Add"}
          </Button>
        </div>
      ) : (
        <p className="text-xs text-zinc-400">
          No org units synced yet — sync Google Workspace settings first.
        </p>
      )}

      {error && <ErrorState>{error}</ErrorState>}
    </Card>
  );
}
