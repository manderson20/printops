"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getPrinterCartridges,
  updatePrinter,
  updatePrinterCartridges,
  type Cartridge,
  type CartridgeColor,
  type Printer,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
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

type Row = { cost: string; yield_pages: string };
type RowsByColor = Record<CartridgeColor, Row>;

function emptyRow(): Row {
  return { cost: "", yield_pages: "" };
}

export function TonerCartridgesCard({
  printer,
  colorSupported,
  onUpdate,
}: {
  printer: Printer;
  colorSupported: boolean;
  onUpdate: (printer: Printer) => void;
}) {
  const printerId = printer.id;
  const isAdmin = useCurrentUser()?.role === "admin";
  const colors: CartridgeColor[] = colorSupported
    ? ["black", "cyan", "magenta", "yellow"]
    : ["black"];
  const [rows, setRows] = useState<RowsByColor | null>(null);
  const [cartridgeModel, setCartridgeModel] = useState(printer.toner_cartridge_model ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getPrinterCartridges(printerId)
      .then((cartridges) => {
        const byColor = Object.fromEntries(cartridges.map((c) => [c.color, c])) as Partial<
          Record<CartridgeColor, Cartridge>
        >;
        const next = Object.fromEntries(
          colors.map((color) => [
            color,
            byColor[color]
              ? { cost: String(byColor[color]!.cost), yield_pages: String(byColor[color]!.yield_pages) }
              : emptyRow(),
          ]),
        ) as RowsByColor;
        setRows(next);
      })
      .catch(() => setRows(Object.fromEntries(colors.map((c) => [c, emptyRow()])) as RowsByColor));
    // colorSupported never changes after the printer loads; printerId identifies the row set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [printerId]);

  function updateField(color: CartridgeColor, field: keyof Row, value: string) {
    setRows((prev) => (prev ? { ...prev, [color]: { ...prev[color], [field]: value } } : prev));
  }

  async function handleSave() {
    if (!rows) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const cartridges: Cartridge[] = colors
        .filter((color) => rows[color].cost.trim() !== "" && rows[color].yield_pages.trim() !== "")
        .map((color) => ({
          color,
          cost: Number(rows[color].cost),
          yield_pages: Number(rows[color].yield_pages),
        }));
      await updatePrinterCartridges(printerId, cartridges);
      const updatedPrinter = await updatePrinter(printerId, {
        toner_cartridge_model: cartridgeModel || null,
      });
      onUpdate(updatedPrinter);
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save cartridges");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-3">Toner Cartridges</CardTitle>
      <p className="mb-3 text-xs text-zinc-500">
        Real cartridge cost and rated page yield, used to compute per-page cost in Print Insights.
        Mono pages price off Black alone; color pages price off every configured cartridge summed.
        Leave a color blank to fall back to the flat per-page rate in the Report Formulas settings.
      </p>

      {rows === null && <Spinner label="Loading cartridges…" />}

      {rows !== null && (
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
            Cartridge Model
            <Input
              type="text"
              placeholder="e.g. TN-227"
              disabled={!isAdmin}
              value={cartridgeModel}
              onChange={(e) => setCartridgeModel(e.target.value)}
            />
          </label>
          {colors.map((color) => (
            <div key={color} className="grid grid-cols-[5rem_1fr_1fr] items-end gap-3">
              <span className="text-sm font-medium text-black dark:text-zinc-50">
                {COLOR_LABELS[color]}
              </span>
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
          ))}
        </div>
      )}

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
