"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getSnmpDefaults,
  updateSnmpDefaults,
  type SnmpDefaults,
  type SnmpVersion,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { PasswordField } from "@/components/ui/PasswordField";
import { Spinner } from "@/components/ui/Spinner";

type SnmpLoadState =
  | { phase: "loading" }
  | { phase: "ok"; settings: SnmpDefaults }
  | { phase: "error"; message: string };

export default function SnmpSettingsPage() {
  const [state, setState] = useState<SnmpLoadState>({ phase: "loading" });
  const [enabled, setEnabled] = useState(false);
  const [version, setVersion] = useState<SnmpVersion>("v2c");
  const [port, setPort] = useState("161");
  const [community, setCommunity] = useState("");
  const [retentionDays, setRetentionDays] = useState("180");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function load() {
    getSnmpDefaults()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setEnabled(settings.enabled);
        setVersion(settings.version);
        setPort(String(settings.port));
        setRetentionDays(String(settings.retention_days));
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load SNMP defaults",
        }),
      );
  }

  useEffect(load, []);

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      const settings = await updateSnmpDefaults({
        enabled,
        version,
        port: Number(port),
        community: community || undefined,
        retention_days: Number(retentionDays),
      });
      setState({ phase: "ok", settings });
      setCommunity("");
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Failed to save SNMP defaults");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-1">Global SNMP Defaults</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Org-wide defaults the counter poll loop uses to read page/copy/print counters from every
        printer over SNMP. Individual printers can override any of these for the odd device
        configured differently.
      </p>

      {state.phase === "loading" && <Spinner label="Loading…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase !== "loading" && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Version">
              <select
                value={version}
                onChange={(e) => setVersion(e.target.value as SnmpVersion)}
                className="rounded border border-black/[.15] bg-transparent px-3 py-2 text-sm text-black dark:border-white/[.2] dark:text-zinc-50"
              >
                <option value="v2c">v2c</option>
                <option value="v1">v1</option>
              </select>
            </Field>
            <Field label="Port">
              <Input value={port} onChange={(e) => setPort(e.target.value)} placeholder="161" />
            </Field>
          </div>

          <PasswordField
            label={
              <>
                Community String{" "}
                {state.phase === "ok" && state.settings.has_community && (
                  <span className="text-xs text-zinc-500">(already set — leave blank to keep)</span>
                )}
              </>
            }
            value={community}
            onChange={setCommunity}
            placeholder={state.phase === "ok" && state.settings.has_community ? "•••••••• (unchanged)" : "public"}
          />

          <Field label="Counter History Retention (days)">
            <Input
              value={retentionDays}
              onChange={(e) => setRetentionDays(e.target.value)}
              placeholder="180"
              className="max-w-[10rem]"
            />
          </Field>

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
                Off = the counter poll loop never runs, even if a community string is configured.
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
