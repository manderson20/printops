"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getSyslogSettings,
  updateSyslogSettings,
  type SyslogSettings,
  type SyslogSeverity,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

const SELECT_CLASS =
  "rounded-lg border border-black/[.15] bg-white px-2 py-1 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50";

const SEVERITIES: SyslogSeverity[] = [
  "emerg",
  "alert",
  "crit",
  "err",
  "warning",
  "notice",
  "info",
  "debug",
];

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; settings: SyslogSettings }
  | { phase: "error"; message: string };

export default function SyslogSettingsPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [enabled, setEnabled] = useState(false);
  const [port, setPort] = useState("514");
  const [minSeverity, setMinSeverity] = useState<SyslogSeverity>("warning");
  const [retentionDays, setRetentionDays] = useState("30");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function load() {
    getSyslogSettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setEnabled(settings.enabled);
        setPort(String(settings.port));
        setMinSeverity(settings.min_severity);
        setRetentionDays(String(settings.retention_days));
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load syslog settings",
        }),
      );
  }

  useEffect(load, []);

  async function handleSave() {
    setSaving(true);
    setSaveError(null);
    try {
      const settings = await updateSyslogSettings({
        enabled,
        port: Number(port),
        min_severity: minSeverity,
        retention_days: Number(retentionDays),
      });
      setState({ phase: "ok", settings });
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Failed to save syslog settings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <div className="mb-1 flex items-center gap-2">
        <CardTitle className="mb-0">Syslog Collection</CardTitle>
        <WikiHelpLink page="Settings-Syslog-Forwarding" />
      </div>
      <p className="mb-4 text-xs text-zinc-500">
        Collects error/event messages printers send over UDP syslog (see infra/syslog-relay/) — a
        place to check for a jam, a fuser warning, or anything else worth diagnosing beyond what
        SNMP counters or IPP status already show. Each printer needs its own syslog/event
        notification target pointed at this box (on the printer&apos;s own admin UI) — this switch
        just controls whether PrintOps stores what arrives. The relay listens regardless of this
        setting, same as LDAP Relay; nothing is actually kept until this is on.
      </p>

      {state.phase === "loading" && <Spinner label="Loading…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase !== "loading" && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Port">
              <Input
                value={port}
                onChange={(e) => setPort(e.target.value)}
                placeholder="514"
              />
            </Field>
            <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
              Minimum severity kept
              <select
                className={SELECT_CLASS}
                value={minSeverity}
                onChange={(e) => setMinSeverity(e.target.value as SyslogSeverity)}
              >
                {SEVERITIES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <Field label="Retention (days)">
            <Input
              type="number"
              value={retentionDays}
              onChange={(e) => setRetentionDays(e.target.value)}
              placeholder="30"
              className="max-w-[10rem]"
            />
          </Field>

          <p className="text-xs text-zinc-500">
            Messages below the minimum severity are dropped as they arrive, not stored and
            filtered later — raw device syslog can be chatty, and events older than the retention
            period are purged automatically.
          </p>

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
                Off = nothing is stored, even if a printer is already sending syslog here.
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
