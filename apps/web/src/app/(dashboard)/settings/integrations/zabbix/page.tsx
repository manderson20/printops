"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  downloadZabbixTemplate,
  getZabbixSettings,
  regenerateZabbixToken,
  updateZabbixSettings,
  type ZabbixSettings,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; settings: ZabbixSettings }
  | { phase: "error"; message: string };

export default function ZabbixSettingsPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [baseUrl, setBaseUrl] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copiedUrl, setCopiedUrl] = useState(false);
  const [copiedToken, setCopiedToken] = useState(false);

  useEffect(() => {
    getZabbixSettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setBaseUrl(settings.base_url ?? window.location.origin);
        setEnabled(settings.enabled);
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load settings",
        }),
      );
  }, []);

  useEffect(() => {
    if (currentUser && currentUser.role !== "admin") {
      router.replace("/settings/integrations");
    }
  }, [currentUser, router]);

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      const settings = await updateZabbixSettings({ base_url: baseUrl || undefined, enabled });
      setState({ phase: "ok", settings });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  async function handleRegenerateToken() {
    if (
      !confirm(
        "Regenerate the API token? The old one stops working immediately — any Zabbix host " +
          "still using it will fail to poll until you update its {$PRINTOPS_API_TOKEN} macro too.",
      )
    ) {
      return;
    }
    setRegenerating(true);
    setActionError(null);
    try {
      const settings = await regenerateZabbixToken();
      setState({ phase: "ok", settings });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to regenerate token");
    } finally {
      setRegenerating(false);
    }
  }

  function copy(value: string, setCopied: (v: boolean) => void) {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  if (state.phase === "loading" || currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading settings…" />;
  }
  if (state.phase === "error") {
    return <ErrorState>{state.message}</ErrorState>;
  }

  const { settings } = state;

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Zabbix</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Lets an external Zabbix server poll fleet-wide print stats and per-printer health from
          PrintOps — the same numbers Live Dashboard and Insights show, viewable from Zabbix
          instead (or alongside it), with your own alerting rules on top if you want them.
        </p>
      </div>

      <Card>
        <details className="group" open={!settings.enabled}>
          <summary className="cursor-pointer list-none">
            <div className="flex items-center justify-between">
              <CardTitle className="mb-0">Setup Guide — Start Here</CardTitle>
              <span className="text-xs text-zinc-500 group-open:hidden">Show</span>
              <span className="hidden text-xs text-zinc-500 group-open:inline">Hide</span>
            </div>
          </summary>
          <div className="mt-4 flex flex-col gap-5 text-sm text-zinc-600 dark:text-zinc-400">
            <div>
              <p className="font-medium text-zinc-700 dark:text-zinc-300">PrintOps side</p>
              <ol className="mt-1 list-decimal space-y-1 pl-5">
                <li>
                  Turn on <strong className="font-medium">Enabled</strong> below and click{" "}
                  <strong className="font-medium">Save</strong> — an API token is generated
                  automatically.
                </li>
                <li>Copy the Base URL and API Token shown below.</li>
                <li>
                  Click <strong className="font-medium">Download Template</strong> — this is the
                  same generic file for every PrintOps install, nothing school-specific is baked
                  into it.
                </li>
              </ol>
            </div>

            <div>
              <p className="font-medium text-zinc-700 dark:text-zinc-300">Zabbix side</p>
              <ol className="mt-1 list-decimal space-y-1 pl-5">
                <li>
                  <strong className="font-medium">Data collection → Templates → Import</strong> —
                  choose the file you downloaded.
                </li>
                <li>
                  <strong className="font-medium">Data collection → Hosts → Create host</strong> —
                  any name is fine (e.g. &quot;PrintOps&quot;).
                </li>
                <li>
                  On that host, add two{" "}
                  <strong className="font-medium">Macros</strong>:{" "}
                  <code className="text-xs">{"{$PRINTOPS_URL}"}</code> (paste the Base URL) and{" "}
                  <code className="text-xs">{"{$PRINTOPS_API_TOKEN}"}</code> (paste the API
                  Token — mark it &quot;Secret text&quot; if Zabbix offers that).
                </li>
                <li>
                  Under <strong className="font-medium">Templates</strong>, link the imported{" "}
                  &quot;PrintOps&quot; template, then save the host.
                </li>
                <li>
                  Printer discovery runs hourly by default — to see printers show up right away,
                  open the host&apos;s discovery rules and use{" "}
                  <strong className="font-medium">Execute now</strong> on &quot;PrintOps: printer
                  discovery&quot;.
                </li>
                <li>
                  Check <strong className="font-medium">Monitoring → Latest data</strong>,
                  filtered to this host, to confirm data is coming in.
                </li>
              </ol>
              <p className="mt-2 text-xs text-zinc-500">
                Exact menu wording/click-path can vary a little by Zabbix version — the template
                itself was built against Zabbix 6.4+ export format; an older Zabbix may need a
                small adjustment on first import.
              </p>
            </div>
          </div>
        </details>
      </Card>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            1
          </span>
          <CardTitle className="mb-0">Connection Details</CardTitle>
        </div>
        <div className="ml-7 flex flex-col gap-4">
          <Field label="Base URL">
            <div className="flex items-center gap-2">
              <Input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://printops.example.org"
                className="flex-1"
              />
              <Button
                type="button"
                variant="secondary"
                onClick={() => copy(baseUrl, setCopiedUrl)}
              >
                {copiedUrl ? "Copied!" : "Copy"}
              </Button>
            </div>
            <span className="text-xs text-zinc-500">
              Prefilled from the address you&apos;re viewing this page at — only change it if
              that&apos;s not the address your Zabbix server can reach PrintOps at over the
              network. Goes in the Zabbix host&apos;s <code className="text-[11px]">{"{$PRINTOPS_URL}"}</code>{" "}
              macro.
            </span>
          </Field>

          <Field label="API Token">
            {settings.api_token ? (
              <div className="flex items-center gap-2">
                <code className="flex-1 overflow-x-auto rounded-lg bg-zinc-100 px-3 py-2 text-[12px] text-zinc-800 dark:bg-white/[.08] dark:text-zinc-200">
                  {settings.api_token}
                </code>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => copy(settings.api_token ?? "", setCopiedToken)}
                >
                  {copiedToken ? "Copied!" : "Copy"}
                </Button>
              </div>
            ) : (
              <span className="text-xs text-zinc-500">
                Generated automatically the first time you enable this below.
              </span>
            )}
            <span className="mt-1 text-xs text-zinc-500">
              Goes in the Zabbix host&apos;s <code className="text-[11px]">{"{$PRINTOPS_API_TOKEN}"}</code>{" "}
              macro. Only valid for these Zabbix polling endpoints — it can&apos;t be used to sign
              into PrintOps or do anything else an admin session could.
            </span>
            {settings.api_token && (
              <Button
                type="button"
                variant="secondary"
                className="mt-2 self-start"
                onClick={handleRegenerateToken}
                disabled={regenerating}
              >
                {regenerating ? "Regenerating…" : "Regenerate Token"}
              </Button>
            )}
          </Field>

          <Button
            type="button"
            variant="secondary"
            className="self-start"
            onClick={() => void downloadZabbixTemplate()}
          >
            Download Template
          </Button>
        </div>
      </Card>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            2
          </span>
          <CardTitle className="mb-0">Save</CardTitle>
        </div>
        <div className="ml-7 flex flex-col gap-4">
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
                Off = every request to the Zabbix polling endpoints is rejected, even with a
                previously-valid token — a real kill switch, not just a UI hint.
              </span>
            </span>
          </label>

          {actionError && <ErrorState>{actionError}</ErrorState>}
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </Card>

      <Card>
        <CardTitle className="mb-4">Status</CardTitle>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm text-zinc-600 dark:text-zinc-400">
          <dt className="font-medium text-zinc-500">Status</dt>
          <dd>
            {settings.enabled ? (
              <Badge tone="success">Enabled</Badge>
            ) : (
              <Badge tone="neutral">Disabled</Badge>
            )}
          </dd>
        </dl>
      </Card>
    </div>
  );
}
