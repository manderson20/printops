"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  checkPrinterCounters,
  getSnmpDefaults,
  updatePrinter,
  updateSnmpDefaults,
  type PageCountConfidence,
  type Printer,
  type SnmpVersion,
  type VendorProfile,
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { PasswordField } from "@/components/ui/PasswordField";
import { ErrorState } from "@/components/ui/EmptyState";

const SELECT_CLASS =
  "rounded-lg border border-black/[.15] bg-white px-2 py-1 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50";

const VENDOR_OPTIONS: { value: VendorProfile | ""; label: string }[] = [
  { value: "", label: "Auto-detect" },
  { value: "canon", label: "Canon" },
  { value: "konica_minolta", label: "Konica Minolta" },
  { value: "hp", label: "HP" },
  { value: "lexmark", label: "Lexmark" },
  { value: "kyocera", label: "Kyocera" },
  { value: "generic", label: "Generic" },
];

// Client-side mirror of app/printers/snmp_counters.py's detect_vendor_profile
// — a display-only hint next to "Auto-detect" in the override select. The
// server (via a live SNMP sysDescr fetch) is the actual source of truth at
// poll time; this can be wrong and that's fine, it's just a hint.
function guessVendorProfile(printer: Printer): VendorProfile {
  const haystack = `${printer.manufacturer ?? ""} ${printer.model ?? ""} ${
    printer.capabilities?.make_model ?? ""
  }`.toLowerCase();
  if (haystack.includes("canon")) return "canon";
  if (
    haystack.includes("konica") ||
    haystack.includes("minolta") ||
    haystack.includes("bizhub")
  )
    return "konica_minolta";
  if (haystack.includes("hp ") || haystack.includes("hewlett") || haystack.includes("laserjet"))
    return "hp";
  if (haystack.includes("lexmark")) return "lexmark";
  if (haystack.includes("kyocera") || haystack.includes("taskalfa")) return "kyocera";
  return "generic";
}

function confidenceBadge(confidence: PageCountConfidence | null) {
  if (confidence === "verified") return <Badge tone="success">Confirmed</Badge>;
  if (confidence === "best_effort") return <Badge tone="warning">Best effort — unconfirmed split</Badge>;
  return null;
}

export function SnmpCountersCard({
  printer,
  onUpdate,
}: {
  printer: Printer;
  onUpdate: (printer: Printer) => void;
}) {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [snmpEnabled, setSnmpEnabled] = useState(printer.snmp_enabled);
  const [port, setPort] = useState(printer.snmp_port?.toString() ?? "");
  const [version, setVersion] = useState<SnmpVersion | "">(printer.snmp_version ?? "");
  const [community, setCommunity] = useState("");
  const [vendorProfile, setVendorProfile] = useState<VendorProfile | "">(
    printer.snmp_vendor_profile ?? "",
  );
  const [savingOverrides, setSavingOverrides] = useState(false);

  const [defaultsEnabled, setDefaultsEnabled] = useState(false);
  const [defaultsVersion, setDefaultsVersion] = useState<SnmpVersion>("v2c");
  const [defaultsPort, setDefaultsPort] = useState("161");
  const [defaultsCommunity, setDefaultsCommunity] = useState("");
  const [savingDefaults, setSavingDefaults] = useState(false);

  useEffect(() => {
    if (!isAdmin) return;
    getSnmpDefaults()
      .then((defaults) => {
        setDefaultsEnabled(defaults.enabled);
        setDefaultsVersion(defaults.version);
        setDefaultsPort(String(defaults.port));
      })
      .catch(() => {});
  }, [isAdmin]);

  async function handleCheckNow() {
    setChecking(true);
    setError(null);
    try {
      const updated = await checkPrinterCounters(printer.id);
      onUpdate(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to check counters");
    } finally {
      setChecking(false);
    }
  }

  async function handleSaveOverrides() {
    setSavingOverrides(true);
    setError(null);
    try {
      const updated = await updatePrinter(printer.id, {
        snmp_enabled: snmpEnabled,
        snmp_port: port ? Number(port) : null,
        snmp_version: version,
        snmp_community: community || undefined,
        snmp_vendor_profile: vendorProfile,
      });
      onUpdate(updated);
      setCommunity("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save SNMP settings");
    } finally {
      setSavingOverrides(false);
    }
  }

  async function handleSaveDefaults() {
    setSavingDefaults(true);
    setError(null);
    try {
      const updated = await updateSnmpDefaults({
        enabled: defaultsEnabled,
        version: defaultsVersion,
        port: Number(defaultsPort),
        community: defaultsCommunity || undefined,
      });
      setDefaultsEnabled(updated.enabled);
      setDefaultsVersion(updated.version);
      setDefaultsPort(String(updated.port));
      setDefaultsCommunity("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save SNMP defaults");
    } finally {
      setSavingDefaults(false);
    }
  }

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <CardTitle>Page Counters</CardTitle>
        {isAdmin && (
          <Button variant="secondary" onClick={handleCheckNow} disabled={checking}>
            {checking ? "Checking…" : "Check Now"}
          </Button>
        )}
      </div>
      <p className="mb-4 text-xs text-zinc-500">
        Polled over SNMP — a lifetime total works on any printer; a copy/print split is only
        available for some vendors and is labeled accordingly.
      </p>

      <div className="flex flex-col gap-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-zinc-700 dark:text-zinc-300">
            Total: {printer.page_count_total ?? "—"}
          </span>
          <span className="text-xs text-zinc-400">
            Checked {formatRelativeTime(printer.page_count_checked_at)}
          </span>
        </div>

        {printer.page_count_confidence && printer.page_count_confidence !== "unsupported" && (
          <div className="flex items-center gap-2">
            <span className="text-zinc-700 dark:text-zinc-300">
              Copy: {printer.page_count_copy ?? "—"} · Print: {printer.page_count_print ?? "—"}
            </span>
            {confidenceBadge(printer.page_count_confidence)}
          </div>
        )}
        {printer.page_count_confidence === "unsupported" && (
          <p className="text-xs text-zinc-500">
            Copy/print breakdown isn&apos;t available for this printer&apos;s vendor yet — total
            only.
          </p>
        )}

        {printer.page_count_error && (
          <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
            {printer.page_count_error}
          </div>
        )}
      </div>

      {isAdmin && (
        <>
          <div className="mt-4 flex flex-col gap-3 border-t border-black/[.08] pt-4 dark:border-white/[.1]">
            <span className="text-xs font-medium text-zinc-500">This printer</span>
            <label className="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300">
              <input
                type="checkbox"
                checked={snmpEnabled}
                onChange={(e) => setSnmpEnabled(e.target.checked)}
              />
              SNMP polling enabled
            </label>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Port override">
                <Input
                  type="number"
                  placeholder="161"
                  value={port}
                  onChange={(e) => setPort(e.target.value)}
                />
              </Field>
              <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
                Version override
                <select
                  className={SELECT_CLASS}
                  value={version}
                  onChange={(e) => setVersion(e.target.value as SnmpVersion | "")}
                >
                  <option value="">Use global default</option>
                  <option value="v1">v1</option>
                  <option value="v2c">v2c</option>
                </select>
              </label>
            </div>
            <PasswordField
              label="Community override"
              value={community}
              onChange={setCommunity}
              placeholder={printer.has_snmp_community ? "•••••••• (set)" : "Use global default"}
            />
            <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
              Vendor profile
              <select
                className={SELECT_CLASS}
                value={vendorProfile}
                onChange={(e) => setVendorProfile(e.target.value as VendorProfile | "")}
              >
                {VENDOR_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.value === ""
                      ? `Auto-detect (currently guessing: ${
                          VENDOR_OPTIONS.find((o) => o.value === guessVendorProfile(printer))
                            ?.label
                        })`
                      : option.label}
                  </option>
                ))}
              </select>
            </label>
            <Button
              variant="secondary"
              className="!px-3 !py-1 text-xs self-start"
              onClick={handleSaveOverrides}
              disabled={savingOverrides}
            >
              {savingOverrides ? "Saving…" : "Save"}
            </Button>
          </div>

          <div className="mt-4 flex flex-col gap-3 border-t border-black/[.08] pt-4 dark:border-white/[.1]">
            <span className="text-xs font-medium text-zinc-500">Global SNMP defaults</span>
            <label className="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300">
              <input
                type="checkbox"
                checked={defaultsEnabled}
                onChange={(e) => setDefaultsEnabled(e.target.checked)}
              />
              Enable SNMP polling fleet-wide
            </label>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Port">
                <Input
                  type="number"
                  value={defaultsPort}
                  onChange={(e) => setDefaultsPort(e.target.value)}
                />
              </Field>
              <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
                Version
                <select
                  className={SELECT_CLASS}
                  value={defaultsVersion}
                  onChange={(e) => setDefaultsVersion(e.target.value as SnmpVersion)}
                >
                  <option value="v1">v1</option>
                  <option value="v2c">v2c</option>
                </select>
              </label>
            </div>
            <PasswordField
              label="Community"
              value={defaultsCommunity}
              onChange={setDefaultsCommunity}
              placeholder="•••••••• (set)"
            />
            <Button
              variant="secondary"
              className="!px-3 !py-1 text-xs self-start"
              onClick={handleSaveDefaults}
              disabled={savingDefaults}
            >
              {savingDefaults ? "Saving…" : "Save"}
            </Button>
          </div>
        </>
      )}

      {error && <ErrorState>{error}</ErrorState>}
    </Card>
  );
}
