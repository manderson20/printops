"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getPrintReleaseSettings,
  regeneratePrinterReleaseToken,
  updatePrintReleaseSettings,
  updatePrinter,
  type Printer,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { ErrorState } from "@/components/ui/EmptyState";

export function PrintReleaseCard({
  printer,
  onUpdate,
}: {
  printer: Printer;
  onUpdate: (printer: Printer) => void;
}) {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [toggling, setToggling] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [holdExpiryHours, setHoldExpiryHours] = useState<string>("");
  const [savingExpiry, setSavingExpiry] = useState(false);

  useEffect(() => {
    if (!isAdmin) return;
    getPrintReleaseSettings()
      .then((settings) => setHoldExpiryHours(String(settings.hold_expiry_hours)))
      .catch(() => {});
  }, [isAdmin]);

  if (!isAdmin) return null;

  const kioskUrl =
    printer.release_token && typeof window !== "undefined"
      ? `${window.location.origin}/release/${printer.release_token}`
      : null;

  async function handleToggle() {
    setToggling(true);
    setError(null);
    try {
      const updated = await updatePrinter(printer.id, {
        release_required: !printer.release_required,
      });
      onUpdate(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update printer");
    } finally {
      setToggling(false);
    }
  }

  async function handleRegenerate() {
    if (!confirm("Regenerate the kiosk link? The old URL will stop working immediately.")) return;
    setRegenerating(true);
    setError(null);
    try {
      const updated = await regeneratePrinterReleaseToken(printer.id);
      onUpdate(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to regenerate link");
    } finally {
      setRegenerating(false);
    }
  }

  async function handleCopy() {
    if (!kioskUrl) return;
    await navigator.clipboard.writeText(kioskUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  async function handleSaveExpiry() {
    const hours = Number(holdExpiryHours);
    if (!Number.isFinite(hours) || hours <= 0) {
      setError("Hold expiry must be a positive number of hours");
      return;
    }
    setSavingExpiry(true);
    setError(null);
    try {
      const settings = await updatePrintReleaseSettings({ hold_expiry_hours: hours });
      setHoldExpiryHours(String(settings.hold_expiry_hours));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save hold expiry");
    } finally {
      setSavingExpiry(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-3">Print Release</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        When enabled, every job sent to this printer is held instead of printed immediately —
        staff release it themselves at a kiosk (any iPad, Chromebook, or browser can load the
        link below) by entering their Google Workspace Employee ID. Prevents accidental prints
        and mixed-up output at shared printers.
      </p>

      <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
        <input
          type="checkbox"
          className="mt-1"
          checked={printer.release_required}
          disabled={toggling}
          onChange={handleToggle}
        />
        <span>Require release for this printer</span>
      </label>

      {printer.release_required && (
        <div className="mt-4 flex flex-col gap-2">
          <span className="text-xs font-medium text-zinc-500">Kiosk URL</span>
          {kioskUrl ? (
            <div className="flex items-center gap-2">
              <code className="flex-1 overflow-x-auto rounded-lg bg-zinc-100 px-3 py-2 text-[12px] text-zinc-800 dark:bg-white/[.08] dark:text-zinc-200">
                {kioskUrl}
              </code>
              <Button variant="secondary" className="!px-3 !py-1 text-xs" onClick={handleCopy}>
                {copied ? "Copied!" : "Copy"}
              </Button>
            </div>
          ) : (
            <span className="text-xs text-zinc-400">No link generated yet.</span>
          )}
          <Button
            variant="secondary"
            className="!px-3 !py-1 text-xs self-start"
            onClick={handleRegenerate}
            disabled={regenerating}
          >
            {regenerating ? "Regenerating…" : "Regenerate Link"}
          </Button>
        </div>
      )}

      <div className="mt-4 flex items-end gap-2 border-t border-black/[.08] pt-4 dark:border-white/[.1]">
        <Field label="Hold expiry (hours)" className="flex-1">
          <Input
            type="number"
            min="0.25"
            step="0.25"
            value={holdExpiryHours}
            onChange={(e) => setHoldExpiryHours(e.target.value)}
          />
        </Field>
        <Button
          variant="secondary"
          className="!px-3 !py-1 text-xs"
          onClick={handleSaveExpiry}
          disabled={savingExpiry}
        >
          {savingExpiry ? "Saving…" : "Save"}
        </Button>
      </div>
      <p className="mt-1 text-xs text-zinc-500">
        Applies to every printer with release required — an unreleased job is cancelled and its
        spooled file deleted after this window.
      </p>

      {error && <ErrorState>{error}</ErrorState>}
    </Card>
  );
}
