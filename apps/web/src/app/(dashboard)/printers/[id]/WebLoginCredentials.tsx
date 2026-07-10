"use client";

import { useState } from "react";
import { ApiError, updatePrinter, type Printer } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { PasswordField } from "@/components/ui/PasswordField";

function CurrentValue({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2">
      <code className="flex-1 overflow-x-auto rounded-lg bg-zinc-100 px-3 py-2 text-[12px] text-zinc-800 dark:bg-white/[.08] dark:text-zinc-200">
        {value}
      </code>
      <Button
        type="button"
        variant="secondary"
        className="!px-3 !py-1 text-xs"
        onClick={() => {
          navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? "Copied!" : "Copy"}
      </Button>
    </div>
  );
}

export function WebLoginCredentialsCard({
  printer,
  onUpdate,
}: {
  printer: Printer;
  onUpdate: (printer: Printer) => void;
}) {
  const isAdmin = useCurrentUser()?.role === "admin";

  const [webUsername, setWebUsername] = useState(printer.web_login_username ?? "");
  const [webPassword, setWebPassword] = useState("");
  const [webSaving, setWebSaving] = useState(false);
  const [webError, setWebError] = useState<string | null>(null);
  const [webSaved, setWebSaved] = useState(false);

  const [scanEmail, setScanEmail] = useState(printer.scan_email_address ?? "");
  const [scanPassword, setScanPassword] = useState("");
  const [scanSaving, setScanSaving] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanSaved, setScanSaved] = useState(false);

  if (!isAdmin) return null;

  async function handleSaveWebLogin() {
    setWebSaving(true);
    setWebError(null);
    setWebSaved(false);
    try {
      const updated = await updatePrinter(printer.id, {
        web_login_username: webUsername,
        web_login_password: webPassword || undefined,
      });
      onUpdate(updated);
      setWebPassword("");
      setWebSaved(true);
    } catch (err) {
      setWebError(
        err instanceof ApiError ? err.message : "Failed to save web login",
      );
    } finally {
      setWebSaving(false);
    }
  }

  async function handleSaveScan() {
    setScanSaving(true);
    setScanError(null);
    setScanSaved(false);
    try {
      const updated = await updatePrinter(printer.id, {
        scan_email_address: scanEmail,
        scan_password: scanPassword || undefined,
      });
      onUpdate(updated);
      setScanPassword("");
      setScanSaved(true);
    } catch (err) {
      setScanError(
        err instanceof ApiError ? err.message : "Failed to save scan credentials",
      );
    } finally {
      setScanSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-3">Reference Credentials</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Stored for reference only — PrintOps never uses these to log into or
        configure anything itself. Visible here only to admins, only on this
        printer&rsquo;s own page.
      </p>

      <div className="flex flex-col gap-4">
        <h3 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
          Web Admin Login
        </h3>
        <p className="-mt-2 text-xs text-zinc-500">
          Some printers&rsquo; web UI needs a username and password; others
          (a bare password prompt) don&rsquo;t have a username at all —
          leave it blank for those.
        </p>
        <Field label="Username (leave blank if this printer doesn't use one)">
          <Input
            value={webUsername}
            onChange={(e) => setWebUsername(e.target.value)}
            placeholder="admin"
          />
        </Field>
        {printer.has_web_login_password && printer.web_login_password && (
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-zinc-500">Current password</span>
            <CurrentValue value={printer.web_login_password} />
          </div>
        )}
        <PasswordField
          label={
            <>
              {printer.has_web_login_password ? "New password" : "Password"}{" "}
              {printer.has_web_login_password && (
                <span className="text-xs text-zinc-500">(leave blank to keep)</span>
              )}
            </>
          }
          value={webPassword}
          onChange={setWebPassword}
          placeholder={printer.has_web_login_password ? "•••••••• (unchanged)" : ""}
        />
        <Button
          className="self-start"
          onClick={handleSaveWebLogin}
          disabled={webSaving}
        >
          {webSaving ? "Saving…" : "Save Web Login"}
        </Button>
        {webError && <ErrorState>{webError}</ErrorState>}
        {webSaved && !webError && (
          <p className="text-xs text-emerald-700 dark:text-emerald-400">Saved.</p>
        )}
      </div>

      <div className="mt-4 flex flex-col gap-4 border-t border-black/[.08] pt-4 dark:border-white/[.1]">
        <h3 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
          Scan-to-Email
        </h3>
        <p className="-mt-2 text-xs text-zinc-500">
          The &ldquo;from&rdquo; address this printer&rsquo;s scanner uses,
          and the password it needs to send through that account.
        </p>
        <Field label="Scan-from email address">
          <Input
            type="email"
            value={scanEmail}
            onChange={(e) => setScanEmail(e.target.value)}
            placeholder="cletus-scan@district.org"
          />
        </Field>
        {printer.has_scan_password && printer.scan_password && (
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-zinc-500">Current password</span>
            <CurrentValue value={printer.scan_password} />
          </div>
        )}
        <PasswordField
          label={
            <>
              {printer.has_scan_password ? "New password" : "Password"}{" "}
              {printer.has_scan_password && (
                <span className="text-xs text-zinc-500">(leave blank to keep)</span>
              )}
            </>
          }
          value={scanPassword}
          onChange={setScanPassword}
          placeholder={printer.has_scan_password ? "•••••••• (unchanged)" : ""}
        />
        <Button className="self-start" onClick={handleSaveScan} disabled={scanSaving}>
          {scanSaving ? "Saving…" : "Save Scan Credentials"}
        </Button>
        {scanError && <ErrorState>{scanError}</ErrorState>}
        {scanSaved && !scanError && (
          <p className="text-xs text-emerald-700 dark:text-emerald-400">Saved.</p>
        )}
      </div>
    </Card>
  );
}
