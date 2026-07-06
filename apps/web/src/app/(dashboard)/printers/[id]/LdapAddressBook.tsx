"use client";

import { useState } from "react";
import { ApiError, updatePrinter, type Printer } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { PasswordField } from "@/components/ui/PasswordField";

export function LdapAddressBookCard({
  printer,
  onUpdate,
}: {
  printer: Printer;
  onUpdate: (printer: Printer) => void;
}) {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [bindUsername, setBindUsername] = useState(
    printer.ldap_bind_username ?? "",
  );
  const [bindPassword, setBindPassword] = useState("");
  const [toggling, setToggling] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  if (!isAdmin) return null;

  async function handleToggle() {
    setToggling(true);
    setError(null);
    try {
      const updated = await updatePrinter(printer.id, {
        ldap_enabled: !printer.ldap_enabled,
      });
      onUpdate(updated);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to update printer",
      );
    } finally {
      setToggling(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await updatePrinter(printer.id, {
        ldap_bind_username: bindUsername,
        ldap_bind_password: bindPassword || undefined,
      });
      onUpdate(updated);
      setBindPassword("");
      setSaved(true);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Failed to save LDAP credentials",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-3">LDAP Address Book</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Lets this printer&rsquo;s scan-to-email address book search PrintOps
        over LDAP instead of connecting directly to Google Workspace. Enter this
        bind username/password into the printer&rsquo;s own LDAP address-book
        settings — PrintOps serves searches from its already-synced Google
        Workspace roster. Also requires the org-wide relay to be turned on
        (Settings &rarr; LDAP Relay).
      </p>

      <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
        <input
          type="checkbox"
          className="mt-1"
          checked={printer.ldap_enabled}
          disabled={toggling}
          onChange={handleToggle}
        />
        <span>Enable LDAP address book for this printer</span>
      </label>

      <div className="mt-4 flex flex-col gap-4 border-t border-black/[.08] pt-4 dark:border-white/[.1]">
        <Field label="Bind username">
          <Input
            value={bindUsername}
            onChange={(e) => setBindUsername(e.target.value)}
            placeholder="front-office-copier"
          />
        </Field>
        <PasswordField
          label={
            <>
              Bind password{" "}
              {printer.has_ldap_bind_password && (
                <span className="text-xs text-zinc-500">
                  (already set — leave blank to keep)
                </span>
              )}
            </>
          }
          value={bindPassword}
          onChange={setBindPassword}
          placeholder={
            printer.has_ldap_bind_password ? "•••••••• (unchanged)" : ""
          }
        />
        <Button className="self-start" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save Credentials"}
        </Button>
      </div>

      {error && <ErrorState>{error}</ErrorState>}
      {saved && !error && (
        <p className="mt-2 text-xs text-emerald-700 dark:text-emerald-400">
          Saved.
        </p>
      )}
    </Card>
  );
}
