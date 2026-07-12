"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  bulkUpdateTonerCartridges,
  listFleetTonerCartridges,
  type FleetCartridge,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState, SuccessState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; cartridges: FleetCartridge[] }
  | { phase: "error"; message: string };

const COLOR_LABELS: Record<FleetCartridge["color"], string> = {
  black: "Black",
  cyan: "Cyan",
  magenta: "Magenta",
  yellow: "Yellow",
};

const CSV_COLUMNS: { header: string; value: (c: FleetCartridge) => string }[] = [
  { header: "Printer", value: (c) => c.printer_name },
  { header: "Building", value: (c) => c.building ?? "" },
  { header: "Room", value: (c) => c.room ?? "" },
  { header: "Manufacturer", value: (c) => c.printer_manufacturer ?? "" },
  { header: "Printer Model", value: (c) => c.printer_model ?? "" },
  { header: "Color", value: (c) => COLOR_LABELS[c.color] },
  { header: "Cartridge Model", value: (c) => c.model ?? "" },
  { header: "Cost ($)", value: (c) => String(c.cost) },
  { header: "Yield (pages)", value: (c) => String(c.yield_pages) },
  {
    header: "Level (%)",
    value: (c) => (c.current_level_percent !== null ? String(c.current_level_percent) : ""),
  },
];

// Quote any field containing a comma, quote, or newline — the minimal
// escaping CSV needs, per RFC 4180. Excel/Sheets/Numbers all read this.
// Same helper as apps/web/src/app/(dashboard)/printers/page.tsx's CSV export.
function csvField(value: string): string {
  if (/[",\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

// Client-side, not a backend endpoint — the (filtered) list is already
// sitting in memory, so an API round-trip would only add latency.
function downloadCartridgesCsv(cartridges: FleetCartridge[]) {
  const lines = [
    CSV_COLUMNS.map((c) => csvField(c.header)).join(","),
    ...cartridges.map((cartridge) =>
      CSV_COLUMNS.map((c) => csvField(c.value(cartridge))).join(","),
    ),
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `printops-toner-cartridges-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

type Edit = { cost: string; yield_pages: string; model: string };
// Bulk-apply is cost/yield only — colors within one printer typically take
// genuinely different cartridge SKUs, so "apply this model to all N" isn't
// a meaningful bulk action the way a shared price/yield is.
type BulkEdit = { cost: string; yield_pages: string };

type Group = {
  key: string;
  label: string;
  deviceLabel: string | null;
  cartridges: FleetCartridge[];
};

function groupByPrinter(cartridges: FleetCartridge[]): Group[] {
  const byPrinter = new Map<string, FleetCartridge[]>();
  for (const cartridge of cartridges) {
    const existing = byPrinter.get(cartridge.printer_id);
    if (existing) {
      existing.push(cartridge);
    } else {
      byPrinter.set(cartridge.printer_id, [cartridge]);
    }
  }
  return [...byPrinter.entries()]
    .map(([printerId, rows]) => {
      const first = rows[0];
      const location = [first.building, first.room].filter(Boolean).join(" / ");
      const deviceLabel =
        [first.printer_manufacturer, first.printer_model].filter(Boolean).join(" ") || null;
      return {
        key: printerId,
        label: location ? `${first.printer_name} (${location})` : first.printer_name,
        deviceLabel,
        cartridges: rows,
      };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

export default function TonerCartridgesSettingsPage() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [filter, setFilter] = useState("");
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [bulkInputs, setBulkInputs] = useState<Record<string, BulkEdit>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  function load() {
    listFleetTonerCartridges()
      .then((cartridges) => setState({ phase: "ok", cartridges }))
      .catch((err: unknown) =>
        setState({
          phase: "error",
          message: err instanceof Error ? err.message : "Failed to load toner cartridges",
        }),
      );
  }

  useEffect(load, []);

  const groups = useMemo(() => {
    if (state.phase !== "ok") return [];
    const needle = filter.trim().toLowerCase();
    const filtered = needle
      ? state.cartridges.filter(
          (c) =>
            c.printer_name.toLowerCase().includes(needle) ||
            (c.model ?? "").toLowerCase().includes(needle) ||
            (c.printer_manufacturer ?? "").toLowerCase().includes(needle) ||
            (c.printer_model ?? "").toLowerCase().includes(needle),
        )
      : state.cartridges;
    return groupByPrinter(filtered);
  }, [state, filter]);

  function defaultEdit(cartridge: FleetCartridge): Edit {
    return {
      cost: String(cartridge.cost),
      yield_pages: String(cartridge.yield_pages),
      model: cartridge.model ?? "",
    };
  }

  function currentValue(cartridge: FleetCartridge, field: keyof Edit): string {
    return edits[cartridge.id]?.[field] ?? defaultEdit(cartridge)[field];
  }

  function updateField(cartridge: FleetCartridge, field: keyof Edit, value: string) {
    setEdits((prev) => ({
      ...prev,
      [cartridge.id]: { ...defaultEdit(cartridge), ...prev[cartridge.id], [field]: value },
    }));
  }

  function applyBulk(group: Group) {
    const bulk = bulkInputs[group.key];
    if (!bulk || (bulk.cost.trim() === "" && bulk.yield_pages.trim() === "")) return;
    setEdits((prev) => {
      const next = { ...prev };
      for (const cartridge of group.cartridges) {
        next[cartridge.id] = {
          ...defaultEdit(cartridge),
          ...prev[cartridge.id],
          cost: bulk.cost.trim() !== "" ? bulk.cost : currentValue(cartridge, "cost"),
          yield_pages:
            bulk.yield_pages.trim() !== "" ? bulk.yield_pages : currentValue(cartridge, "yield_pages"),
        };
      }
      return next;
    });
  }

  async function handleSave() {
    const updates = Object.entries(edits).map(([id, edit]) => ({
      id,
      cost: Number(edit.cost) || 0,
      yield_pages: Number(edit.yield_pages) || 0,
      model: edit.model.trim() || null,
    }));
    if (updates.length === 0) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      await bulkUpdateTonerCartridges(updates);
      setEdits({});
      setSaved(true);
      load();
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Failed to save changes");
    } finally {
      setSaving(false);
    }
  }

  const dirtyCount = Object.keys(edits).length;
  const visibleCartridges = useMemo(() => groups.flatMap((g) => g.cartridges), [groups]);

  return (
    <div className="flex flex-col gap-6">
      <Card className="print:hidden">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <CardTitle>Toner Cartridges</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              type="text"
              placeholder="Filter by printer, brand/model, or cartridge model…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="max-w-xs"
            />
            <Button
              variant="secondary"
              className="!px-3 !py-1.5 text-xs"
              onClick={() => downloadCartridgesCsv(visibleCartridges)}
            >
              Export CSV
            </Button>
            <Button
              variant="secondary"
              className="!px-3 !py-1.5 text-xs"
              onClick={() => window.print()}
            >
              Print / Save as PDF
            </Button>
          </div>
        </div>
        <p className="text-xs text-zinc-500">
          Every printer&apos;s cartridge cost/yield in one place, grouped by printer — each
          cartridge shows its detected model so you can spot ones shared across printers.
          Changes aren&apos;t saved until you click Save Changes below. Export CSV or Print /
          Save as PDF respect the current filter.
        </p>
      </Card>

      {state.phase === "loading" && <Spinner label="Loading toner cartridges…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && groups.length === 0 && (
        <EmptyState>No toner cartridges found yet.</EmptyState>
      )}

      {state.phase === "ok" &&
        groups.map((group) => (
          <Card key={group.key}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <CardTitle>
                  {group.label}{" "}
                  <span className="text-xs font-normal text-zinc-500">
                    ({group.cartridges.length} cartridge{group.cartridges.length === 1 ? "" : "s"})
                  </span>
                </CardTitle>
                {group.deviceLabel && (
                  <p className="text-xs text-zinc-500">{group.deviceLabel}</p>
                )}
              </div>
              <div className="flex items-end gap-2 print:hidden">
                <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
                  Cost ($)
                  <Input
                    type="number"
                    step="0.01"
                    min="0"
                    className="w-24"
                    value={bulkInputs[group.key]?.cost ?? ""}
                    onChange={(e) =>
                      setBulkInputs((prev) => ({
                        ...prev,
                        [group.key]: { cost: e.target.value, yield_pages: prev[group.key]?.yield_pages ?? "" },
                      }))
                    }
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
                  Yield (pages)
                  <Input
                    type="number"
                    min="0"
                    className="w-28"
                    value={bulkInputs[group.key]?.yield_pages ?? ""}
                    onChange={(e) =>
                      setBulkInputs((prev) => ({
                        ...prev,
                        [group.key]: { cost: prev[group.key]?.cost ?? "", yield_pages: e.target.value },
                      }))
                    }
                  />
                </label>
                <Button variant="secondary" className="!px-3 !py-1.5 text-xs" onClick={() => applyBulk(group)}>
                  Apply to all {group.cartridges.length}
                </Button>
              </div>
            </div>

            <div className="flex flex-col gap-1">
              {group.cartridges.map((cartridge) => (
                <div
                  key={cartridge.id}
                  className="grid grid-cols-[5rem_1fr_auto_auto_auto] items-center gap-3 border-t border-black/[.08] py-2 first:border-t-0 dark:border-white/[.1]"
                >
                  <Badge tone="neutral">{COLOR_LABELS[cartridge.color]}</Badge>
                  <Input
                    type="text"
                    placeholder="e.g. TN-227C"
                    value={currentValue(cartridge, "model")}
                    onChange={(e) => updateField(cartridge, "model", e.target.value)}
                  />
                  <Input
                    type="number"
                    step="0.01"
                    min="0"
                    className="w-24"
                    value={currentValue(cartridge, "cost")}
                    onChange={(e) => updateField(cartridge, "cost", e.target.value)}
                  />
                  <Input
                    type="number"
                    min="0"
                    className="w-28"
                    value={currentValue(cartridge, "yield_pages")}
                    onChange={(e) => updateField(cartridge, "yield_pages", e.target.value)}
                  />
                  <span className="text-xs text-zinc-500">
                    {cartridge.current_level_percent !== null
                      ? `${cartridge.current_level_percent}% left`
                      : "—"}
                  </span>
                </div>
              ))}
            </div>
          </Card>
        ))}

      {state.phase === "ok" && groups.length > 0 && (
        <div className="sticky bottom-4 flex items-center gap-3 rounded-lg border border-black/[.1] bg-white p-3 shadow-lg print:hidden dark:border-white/[.15] dark:bg-black">
          <Button onClick={handleSave} disabled={saving || dirtyCount === 0}>
            {saving ? "Saving…" : `Save Changes${dirtyCount > 0 ? ` (${dirtyCount})` : ""}`}
          </Button>
          {saveError && <ErrorState>{saveError}</ErrorState>}
          {saved && !saveError && <SuccessState>Saved.</SuccessState>}
        </div>
      )}
    </div>
  );
}
