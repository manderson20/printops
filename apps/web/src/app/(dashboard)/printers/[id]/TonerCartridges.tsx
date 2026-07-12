"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  detectPrinterCartridges,
  getPrinterCartridges,
  updatePrinterCartridges,
  type Cartridge,
  type CartridgeColor,
  type DetectedSupply,
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Field";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

const COLOR_LABELS: Record<CartridgeColor, string> = {
  black: "Black",
  cyan: "Cyan",
  magenta: "Magenta",
  yellow: "Yellow",
};

type Row = {
  model: string;
  cost: string;
  yield_pages: string;
  detected_description: string | null;
  detected_high_capacity: boolean | null;
  detected_at: string | null;
};
type RowsByColor = Record<CartridgeColor, Row>;

function emptyRow(): Row {
  return {
    model: "",
    cost: "",
    yield_pages: "",
    detected_description: null,
    detected_high_capacity: null,
    detected_at: null,
  };
}

function rowFromCartridge(cartridge: Cartridge): Row {
  return {
    model: cartridge.model ?? "",
    cost: String(cartridge.cost),
    yield_pages: String(cartridge.yield_pages),
    detected_description: cartridge.detected_description,
    detected_high_capacity: cartridge.detected_high_capacity,
    detected_at: cartridge.detected_at,
  };
}

export function TonerCartridgesCard({
  printerId,
  colorSupported,
}: {
  printerId: string;
  colorSupported: boolean;
}) {
  const isAdmin = useCurrentUser()?.role === "admin";
  const colors: CartridgeColor[] = colorSupported
    ? ["black", "cyan", "magenta", "yellow"]
    : ["black"];
  const [rows, setRows] = useState<RowsByColor | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [detectError, setDetectError] = useState<string | null>(null);
  const [unmatched, setUnmatched] = useState<DetectedSupply[]>([]);

  function loadRows() {
    getPrinterCartridges(printerId)
      .then((cartridges) => {
        const byColor = Object.fromEntries(cartridges.map((c) => [c.color, c])) as Partial<
          Record<CartridgeColor, Cartridge>
        >;
        const next = Object.fromEntries(
          colors.map((color) => [
            color,
            byColor[color] ? rowFromCartridge(byColor[color]!) : emptyRow(),
          ]),
        ) as RowsByColor;
        setRows(next);
      })
      .catch(() => setRows(Object.fromEntries(colors.map((c) => [c, emptyRow()])) as RowsByColor));
  }

  useEffect(() => {
    loadRows();
    // colorSupported never changes after the printer loads; printerId identifies the row set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [printerId]);

  function updateField(color: CartridgeColor, field: "model" | "cost" | "yield_pages", value: string) {
    setRows((prev) => (prev ? { ...prev, [color]: { ...prev[color], [field]: value } } : prev));
  }

  async function handleSave() {
    if (!rows) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const cartridges = colors
        .filter(
          (color) =>
            rows[color].model.trim() !== "" ||
            rows[color].cost.trim() !== "" ||
            rows[color].yield_pages.trim() !== "",
        )
        .map((color) => ({
          color,
          model: rows[color].model.trim() || null,
          cost: Number(rows[color].cost) || 0,
          yield_pages: Number(rows[color].yield_pages) || 0,
        }));
      await updatePrinterCartridges(printerId, cartridges);
      setSaved(true);
      loadRows(); // pick up detected_* fields carried over by the PUT
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save cartridges");
    } finally {
      setSaving(false);
    }
  }

  async function handleDetect() {
    setDetecting(true);
    setDetectError(null);
    setUnmatched([]);
    try {
      const result = await detectPrinterCartridges(printerId);
      const byColor = Object.fromEntries(result.cartridges.map((c) => [c.color, c])) as Partial<
        Record<CartridgeColor, Cartridge>
      >;
      setRows((prev) => {
        const base = prev ?? (Object.fromEntries(colors.map((c) => [c, emptyRow()])) as RowsByColor);
        return Object.fromEntries(
          colors.map((color) => [
            color,
            byColor[color] ? rowFromCartridge(byColor[color]!) : base[color],
          ]),
        ) as RowsByColor;
      });
      setUnmatched(result.unmatched);
    } catch (err) {
      setDetectError(err instanceof ApiError ? err.message : "SNMP cartridge detection failed");
    } finally {
      setDetecting(false);
    }
  }

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <CardTitle>Toner Cartridges</CardTitle>
        {isAdmin && (
          <Button variant="secondary" onClick={handleDetect} disabled={detecting}>
            {detecting ? "Detecting…" : "Detect via SNMP"}
          </Button>
        )}
      </div>
      <p className="mb-3 text-xs text-zinc-500">
        Real cartridge cost and rated page yield, used to compute per-page cost in Print Insights.
        Mono pages price off Black alone; color pages price off every configured cartridge summed.
        Leave a color blank to fall back to the flat per-page rate in the Report Formulas settings.
        &ldquo;Detect via SNMP&rdquo; reads each cartridge&apos;s description straight off the
        device and guesses color/high-capacity from it — a best-effort read, not a confirmed fact,
        so double-check it against the physical cartridge before trusting it blindly.
      </p>

      {rows === null && <Spinner label="Loading cartridges…" />}

      {rows !== null && (
        <div className="flex flex-col gap-3">
          {colors.map((color) => (
            <div key={color} className="flex flex-col gap-1 border-t border-black/[.08] pt-3 first:border-t-0 first:pt-0 dark:border-white/[.1]">
              <div className="grid grid-cols-[5rem_1fr_1fr_1fr] items-end gap-3">
                <span className="text-sm font-medium text-black dark:text-zinc-50">
                  {COLOR_LABELS[color]}
                </span>
                <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
                  Model
                  <Input
                    type="text"
                    placeholder="e.g. TN-227C"
                    disabled={!isAdmin}
                    value={rows[color].model}
                    onChange={(e) => updateField(color, "model", e.target.value)}
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
                  Cost ($)
                  <Input
                    type="number"
                    step="0.01"
                    min="0"
                    disabled={!isAdmin}
                    value={rows[color].cost}
                    onChange={(e) => updateField(color, "cost", e.target.value)}
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
                  Yield (pages)
                  <Input
                    type="number"
                    min="0"
                    disabled={!isAdmin}
                    value={rows[color].yield_pages}
                    onChange={(e) => updateField(color, "yield_pages", e.target.value)}
                  />
                </label>
              </div>
              {rows[color].detected_description && (
                <div className="flex flex-wrap items-center gap-2 pl-[calc(5rem+0.75rem)] text-xs text-zinc-500">
                  <span>Detected: {rows[color].detected_description}</span>
                  {rows[color].detected_high_capacity && (
                    <Badge tone="info">High Capacity</Badge>
                  )}
                  <span className="text-zinc-400">
                    ({formatRelativeTime(rows[color].detected_at)})
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {unmatched.length > 0 && (
        <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          <p className="font-medium">
            {unmatched.length} supply item{unmatched.length === 1 ? "" : "s"} detected but
            couldn&apos;t be matched to a color:
          </p>
          <ul className="mt-1 list-inside list-disc">
            {unmatched.map((supply, i) => (
              <li key={i}>{supply.description}</li>
            ))}
          </ul>
        </div>
      )}

      {detectError && <ErrorState>{detectError}</ErrorState>}
      {error && <ErrorState>{error}</ErrorState>}
      {saved && !error && <p className="mt-2 text-xs text-emerald-700 dark:text-emerald-400">Saved.</p>}

      {isAdmin && rows !== null && (
        <Button onClick={handleSave} disabled={saving} className="mt-4">
          {saving ? "Saving…" : "Save Cartridges"}
        </Button>
      )}
    </Card>
  );
}
