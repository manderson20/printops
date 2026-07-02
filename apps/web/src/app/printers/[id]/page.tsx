"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ApiError,
  deletePrinter,
  getPrinter,
  rediscoverPrinter,
  updatePrinter,
  type Printer,
} from "@/lib/api";
import { capabilityBadges } from "@/lib/capabilities";
import { useAuthGuard } from "@/lib/useAuthGuard";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; printer: Printer }
  | { phase: "error"; message: string };

const EDITABLE_FIELDS = [
  ["name", "Name"],
  ["ip_address", "IP Address"],
  ["manufacturer", "Manufacturer"],
  ["model", "Model"],
  ["hostname", "Hostname"],
  ["serial_number", "Serial Number"],
  ["building", "Building"],
  ["room", "Room"],
  ["department", "Department"],
  ["notes", "Notes"],
] as const;

export default function PrinterDetailPage() {
  useAuthGuard();
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [form, setForm] = useState<Record<string, string>>({});
  const [airprintEnabled, setAirprintEnabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rediscovering, setRediscovering] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    getPrinter(params.id)
      .then((printer) => {
        setState({ phase: "ok", printer });
        setForm(
          Object.fromEntries(
            EDITABLE_FIELDS.map(([field]) => [field, (printer as never)[field] ?? ""]),
          ),
        );
        setAirprintEnabled(printer.airprint_enabled);
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load printer",
        }),
      );
  }, [params.id]);

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      const printer = await updatePrinter(params.id, { ...form, airprint_enabled: airprintEnabled });
      setState({ phase: "ok", printer });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to save changes");
    } finally {
      setSaving(false);
    }
  }

  async function handleRediscover() {
    setRediscovering(true);
    setActionError(null);
    try {
      const printer = await rediscoverPrinter(params.id);
      setState({ phase: "ok", printer });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Rediscovery failed");
    } finally {
      setRediscovering(false);
    }
  }

  async function handleDelete() {
    if (!confirm("Delete this printer?")) return;
    await deletePrinter(params.id);
    router.push("/printers");
  }

  if (state.phase === "loading") {
    return <p className="p-8 text-zinc-500">Loading…</p>;
  }
  if (state.phase === "error") {
    return <p className="p-8 text-red-600 dark:text-red-400">{state.message}</p>;
  }

  const { printer } = state;
  const caps = printer.capabilities;
  const inputClass =
    "rounded border border-black/[.15] bg-transparent px-3 py-2 text-black dark:border-white/[.2] dark:text-zinc-50";

  return (
    <div className="flex flex-1 justify-center bg-zinc-50 p-8 font-sans dark:bg-black">
      <div className="flex w-full max-w-2xl flex-col gap-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">{printer.name}</h1>
          <button
            onClick={handleDelete}
            className="rounded-full border border-red-300 px-4 py-2 text-sm font-medium text-red-600 dark:border-red-900 dark:text-red-400"
          >
            Delete
          </button>
        </div>

        <section className="rounded-xl border border-black/[.08] bg-white p-6 dark:border-white/[.145] dark:bg-black">
          <h2 className="mb-4 text-sm font-semibold uppercase text-zinc-500">Details</h2>
          <div className="grid grid-cols-2 gap-4">
            {EDITABLE_FIELDS.map(([field, label]) => (
              <label key={field} className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
                {label}
                <input
                  className={inputClass}
                  value={form[field] ?? ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, [field]: e.target.value }))}
                />
              </label>
            ))}
          </div>

          <label className="mt-4 flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              className="mt-1"
              checked={airprintEnabled}
              onChange={(e) => setAirprintEnabled(e.target.checked)}
            />
            <span>
              Discoverable via AirPrint (Bonjour)
              <br />
              <span className="text-xs text-zinc-500">
                Off = hidden from automatic discovery on Macs/iPads; only devices explicitly
                configured (e.g. via MDM) can print to it. Recommended off for printers
                handling confidential documents.
              </span>
            </span>
          </label>

          {actionError && <p className="mt-3 text-sm text-red-600 dark:text-red-400">{actionError}</p>}
          <button
            onClick={handleSave}
            disabled={saving}
            className="mt-4 rounded-full bg-foreground px-5 py-2 text-sm font-medium text-background hover:bg-[#383838] disabled:opacity-50 dark:hover:bg-[#ccc]"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </section>

        <section className="rounded-xl border border-black/[.08] bg-white p-6 dark:border-white/[.145] dark:bg-black">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase text-zinc-500">
              Discovered Capabilities
            </h2>
            <button
              onClick={handleRediscover}
              disabled={rediscovering}
              className="rounded-full border border-black/[.15] px-4 py-1.5 text-xs font-medium text-black disabled:opacity-50 dark:border-white/[.2] dark:text-zinc-50"
            >
              {rediscovering ? "Probing…" : "Rediscover"}
            </button>
          </div>

          {printer.capabilities_error && (
            <div className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
              <p className="font-medium">Printer saved, but capabilities couldn&apos;t be detected.</p>
              <p className="mt-1 text-amber-800 dark:text-amber-300">{printer.capabilities_error}</p>
              <p className="mt-1 text-amber-800 dark:text-amber-300">
                Common cause: IPP is disabled on the device by default (true for most Canon,
                and many other, printers) — enable it in the printer&apos;s own admin page, then
                click Rediscover below.
              </p>
            </div>
          )}

          {!caps && !printer.capabilities_error && (
            <p className="text-sm text-zinc-500">No capabilities detected yet.</p>
          )}

          {caps && (
            <div className="flex flex-col gap-3 text-sm">
              <div className="flex flex-wrap gap-1">
                {capabilityBadges(caps).map((badge) => (
                  <span
                    key={badge}
                    className="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-800 dark:bg-blue-950 dark:text-blue-300"
                  >
                    {badge}
                  </span>
                ))}
              </div>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-zinc-600 dark:text-zinc-400">
                <dt className="font-medium text-zinc-500">Make/Model</dt>
                <dd>{caps.make_model ?? "—"}</dd>
                <dt className="font-medium text-zinc-500">Max Copies</dt>
                <dd>{caps.copies_max ?? "—"}</dd>
                <dt className="font-medium text-zinc-500">Media Sizes</dt>
                <dd>{caps.media_sizes.join(", ") || "—"}</dd>
                <dt className="font-medium text-zinc-500">Media Sources</dt>
                <dd>{caps.media_sources.join(", ") || "—"}</dd>
                <dt className="font-medium text-zinc-500">Output Bins</dt>
                <dd>{caps.output_bins.join(", ") || "—"}</dd>
              </dl>
              {printer.capabilities_detected_at && (
                <p className="text-xs text-zinc-400">
                  Last probed {new Date(printer.capabilities_detected_at).toLocaleString()}
                </p>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
