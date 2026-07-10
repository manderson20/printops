"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  createPrinterReleaseBypass,
  deletePrinterReleaseBypass,
  getPrintReleaseSettings,
  listGoogleWorkspaceUsers,
  listPrinterReleaseBypasses,
  regeneratePrinterReleaseToken,
  updatePrintReleaseSettings,
  updatePrinter,
  type GoogleWorkspaceUserEntry,
  type Printer,
  type PrinterReleaseBypass,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { ErrorState } from "@/components/ui/EmptyState";

const RELEASE_BYPASS_ROSTER_DATALIST_ID = "printer-release-bypass-roster";

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

  const [bypasses, setBypasses] = useState<PrinterReleaseBypass[] | null>(null);
  const [roster, setRoster] = useState<GoogleWorkspaceUserEntry[]>([]);
  const [bypassEmail, setBypassEmail] = useState("");
  const [bypassSaving, setBypassSaving] = useState(false);
  const [bypassError, setBypassError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAdmin) return;
    getPrintReleaseSettings()
      .then((settings) => setHoldExpiryHours(String(settings.hold_expiry_hours)))
      .catch(() => {});
  }, [isAdmin]);

  function loadBypasses() {
    if (!isAdmin) return;
    listPrinterReleaseBypasses(printer.id)
      .then(setBypasses)
      .catch(() => setBypasses([]));
  }

  useEffect(loadBypasses, [isAdmin, printer.id]);
  useEffect(() => {
    if (!isAdmin) return;
    listGoogleWorkspaceUsers()
      .then(setRoster)
      .catch(() => setRoster([]));
  }, [isAdmin]);

  if (!isAdmin) return null;

  async function handleAddBypass() {
    setBypassSaving(true);
    setBypassError(null);
    try {
      await createPrinterReleaseBypass(printer.id, bypassEmail.trim());
      setBypassEmail("");
      loadBypasses();
    } catch (err) {
      setBypassError(err instanceof ApiError ? err.message : "Failed to add bypass");
    } finally {
      setBypassSaving(false);
    }
  }

  async function handleRemoveBypass(bypassId: string) {
    setBypassSaving(true);
    setBypassError(null);
    try {
      await deletePrinterReleaseBypass(printer.id, bypassId);
      loadBypasses();
    } catch (err) {
      setBypassError(err instanceof ApiError ? err.message : "Failed to remove bypass");
    } finally {
      setBypassSaving(false);
    }
  }

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

          <div className="mt-2 border-t border-black/[.08] pt-4 dark:border-white/[.1]">
            <span className="text-xs font-medium text-zinc-500">
              Release Bypass — skips the kiosk for specific staff
            </span>
            <p className="mt-1 text-xs text-zinc-500">
              These users&rsquo; jobs at this printer print immediately, without ever being held
              — e.g. someone who sits right next to it. Everyone else still releases their own
              jobs normally.
            </p>

            {bypasses !== null && bypasses.length > 0 && (
              <ul className="mt-3 flex flex-col gap-1">
                {bypasses.map((bypass) => (
                  <li
                    key={bypass.id}
                    className="flex items-center justify-between rounded border border-black/[.08] px-2 py-1 text-xs dark:border-white/[.1]"
                  >
                    <span className="font-mono">{bypass.user_email}</span>
                    <Button
                      variant="danger"
                      className="!px-2 !py-0.5 text-xs"
                      disabled={bypassSaving}
                      onClick={() => handleRemoveBypass(bypass.id)}
                    >
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-3 flex items-end gap-2">
              <label className="flex flex-1 flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
                User
                <input
                  list={RELEASE_BYPASS_ROSTER_DATALIST_ID}
                  value={bypassEmail}
                  onChange={(e) => setBypassEmail(e.target.value)}
                  placeholder="user@domain.com"
                  className="rounded border border-black/[.15] bg-transparent px-2 py-1.5 text-sm text-black dark:border-white/[.2] dark:text-zinc-50"
                />
              </label>
              <Button
                variant="secondary"
                className="!px-3 !py-1 text-xs"
                onClick={handleAddBypass}
                disabled={bypassSaving || !bypassEmail.trim()}
              >
                {bypassSaving ? "Adding…" : "Add"}
              </Button>
            </div>
            {bypassError && <ErrorState>{bypassError}</ErrorState>}
            <datalist id={RELEASE_BYPASS_ROSTER_DATALIST_ID}>
              {roster.map((u) => (
                <option key={u.email} value={u.email}>
                  {u.name ?? u.email}
                </option>
              ))}
            </datalist>
          </div>
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
