"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getServerSettings,
  syncServerSettingsNow,
  updateServerSettings,
  type ServerSettings,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; settings: ServerSettings }
  | { phase: "error"; message: string };

export default function ServerSettingsPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [hostname, setHostname] = useState("");
  const [requireEncryption, setRequireEncryption] = useState(false);
  const [advertiseIpps, setAdvertiseIpps] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);

  function load() {
    getServerSettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setHostname(settings.hostname);
        setRequireEncryption(settings.require_encryption);
        setAdvertiseIpps(settings.advertise_ipps);
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load server settings",
        }),
      );
  }

  useEffect(load, []);

  async function handleSyncNow() {
    setSyncing(true);
    setSyncError(null);
    try {
      const settings = await syncServerSettingsNow();
      setState({ phase: "ok", settings });
    } catch (err) {
      setSyncError(err instanceof ApiError ? err.message : "Failed to sync");
    } finally {
      setSyncing(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      const settings = await updateServerSettings({
        hostname,
        require_encryption: requireEncryption,
        advertise_ipps: advertiseIpps,
      });
      setState({ phase: "ok", settings });
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Failed to save server settings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-1">Server</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        The print server&rsquo;s own client-facing hostname and TLS configuration —
        distinct from a printer&rsquo;s own &quot;Connect via TLS&quot; setting (Printers &gt; a
        printer &gt; Overview), which is the connection from PrintOps to that one real device.
        This is the connection from client devices (Macs, iPads, Chromebooks) to PrintOps itself.
      </p>

      {state.phase === "loading" && <Spinner label="Loading…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase !== "loading" && (
        <div className="flex flex-col gap-4">
          <Field label="Hostname">
            <Input
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              placeholder="print.example.org"
            />
            <span className="mt-1 text-xs text-zinc-500">
              Baked into MDM connection info for newly-configured printer queues going
              forward — doesn&rsquo;t retroactively change anything already set up on client
              devices. Must have a DNS record pointing at this server, or CUPS will reject
              requests for it.
            </span>
          </Field>

          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-zinc-500">Certificate</span>
              <Button
                variant="secondary"
                className="!px-3 !py-1 text-xs"
                onClick={handleSyncNow}
                disabled={syncing}
              >
                {syncing ? "Syncing…" : "Sync Now"}
              </Button>
            </div>
            {state.phase === "ok" && state.settings.certificate ? (
              <div className="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
                <p>Issued by {state.settings.certificate.issuer}</p>
                <p className="text-xs text-zinc-500">
                  Expires {new Date(state.settings.certificate.expires_at).toLocaleDateString()}
                  {" "}({state.settings.certificate.days_remaining} days) — renews automatically
                  via Caddy; PrintOps picks up the new one on save, by a daily background
                  check, or immediately if you hit Sync Now (e.g. right after changing the
                  hostname, or after Caddy just renewed).
                </p>
              </div>
            ) : (
              <p className="mt-1 text-xs text-zinc-500">
                Not synced yet — save below, or hit Sync Now, to sync a certificate if one is
                available for this hostname.
              </p>
            )}
            <p className="mt-1 text-xs text-zinc-500">
              Syncing briefly restarts the CUPS service on the print server (a few seconds) —
              CUPS only picks up a new certificate on a real restart, not a lighter config
              reload.
            </p>
          </div>

          {syncError && <ErrorState>{syncError}</ErrorState>}
          {state.phase === "ok" && state.settings.sync_error && (
            <ErrorState>{state.settings.sync_error}</ErrorState>
          )}

          <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              className="mt-1"
              checked={requireEncryption}
              onChange={(e) => setRequireEncryption(e.target.checked)}
            />
            <span>
              Require encrypted client connections
              <br />
              <span className="text-xs text-zinc-500">
                Off by default. Getting a real certificate synced above is a pure improvement
                with no effect on existing plaintext clients — this is the separate, riskier
                lever that can actually break an unusual client&rsquo;s printing if it doesn&rsquo;t
                handle IPPS well. Test against a real device before turning this on.
              </span>
            </span>
          </label>

          <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              className="mt-1"
              checked={advertiseIpps}
              onChange={(e) => setAdvertiseIpps(e.target.checked)}
            />
            <span>
              Advertise encrypted printing via AirPrint (Bonjour)
              <br />
              <span className="text-xs text-zinc-500">
                Off by default. Publishes a second, secure discovery entry alongside the
                existing one for every AirPrint-discoverable printer — additive, doesn&rsquo;t
                remove the plaintext entry.
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
