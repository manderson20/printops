"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getLdapRelaySettings,
  updateLdapRelaySettings,
  type LdapRelaySettings,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; settings: LdapRelaySettings }
  | { phase: "error"; message: string };

export default function LdapRelaySettingsPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [enabled, setEnabled] = useState(false);
  const [baseDn, setBaseDn] = useState("");
  const [port, setPort] = useState("389");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function load() {
    getLdapRelaySettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setEnabled(settings.enabled);
        setBaseDn(settings.base_dn);
        setPort(String(settings.port));
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message:
            error instanceof Error
              ? error.message
              : "Failed to load LDAP relay settings",
        }),
      );
  }

  useEffect(load, []);

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      const settings = await updateLdapRelaySettings({
        enabled,
        base_dn: baseDn,
        port: Number(port),
      });
      setState({ phase: "ok", settings });
    } catch (err) {
      setSaveError(
        err instanceof ApiError
          ? err.message
          : "Failed to save LDAP relay settings",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-1">LDAP Address-Book Relay</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Lets copiers do scan-to-email address-book lookups against PrintOps
        instead of each one holding its own direct LDAP connection to Google
        Workspace. Served entirely from the Google Workspace roster PrintOps
        already syncs (at most ~15 minutes stale) — no live Google call happens
        per search. Configure each printer&rsquo;s own bind credentials on its
        detail page; nothing is served here until that printer&rsquo;s own LDAP
        toggle is also on. Plain LDAP, not LDAPS — bind credentials travel in
        cleartext on your internal network, same trust model this system already
        uses for IPP.
      </p>

      {state.phase === "loading" && <Spinner label="Loading…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase !== "loading" && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Base DN">
              <Input
                value={baseDn}
                onChange={(e) => setBaseDn(e.target.value)}
                placeholder="dc=yourdistrict,dc=org"
              />
            </Field>
            <Field label="Port">
              <Input
                value={port}
                onChange={(e) => setPort(e.target.value)}
                placeholder="389"
              />
            </Field>
          </div>

          <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              className="mt-1"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            <span>
              Enabled
              <br />
              <span className="text-xs text-zinc-500">
                Off = the relay never listens/serves, even if printers have bind
                credentials configured.
              </span>
            </span>
          </label>

          {saveError && <ErrorState>{saveError}</ErrorState>}
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      )}
    </Card>
  );
}
