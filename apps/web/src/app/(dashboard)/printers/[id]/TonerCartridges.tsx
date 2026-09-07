"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  detectPrinterCartridges,
  getPrinterCartridges,
  getReportFormulaSettings,
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

/** What a page costs in toner, from cartridge price and rated yield.
 *
 * The same rule the reports use, computed here from what is typed so the
 * effect of a price or yield is visible before it is saved: mono prices off
 * Black alone, colour off every configured cartridge summed, because a colour
 * page draws from all four.
 *
 * This is the *rated* figure — cost divided by the yield printed on the box,
 * which manufacturers quote at ISO/IEC 19798's 5% coverage. A page of dense
 * graphics costs several times this and a mostly-blank page a fraction of it,
 * so it is an average, not a measurement of any particular page.
 */
function tonerCostPerPage(rows: Record<CartridgeColor, Row>): {
  mono: number | null;
  color: number | null;
} {
  const perColor = (color: CartridgeColor): number | null => {
    const cost = Number(rows[color].cost);
    const yieldPages = Number(rows[color].yield_pages);
    if (!Number.isFinite(cost) || !Number.isFinite(yieldPages)) return null;
    if (cost <= 0 || yieldPages <= 0) return null;
    return cost / yieldPages;
  };

  const black = perColor("black");
  const configured = (["black", "cyan", "magenta", "yellow"] as CartridgeColor[])
    .map(perColor)
    .filter((value): value is number => value !== null);

  return {
    mono: black,
    // Only meaningful once something beyond Black is priced; otherwise it
    // would just repeat the mono figure and look like a colour page costs the
    // same, which is the one thing this panel exists to disprove.
    color: configured.length > 1 ? configured.reduce((sum, v) => sum + v, 0) : null,
  };
}

function money(value: number): string {
  // Four places: a mono page is often under a cent, and rounding to two would
  // print "$0.00" for every printer in the estate.
  return `$${value.toFixed(4)}`;
}

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
  warning_threshold_percent: string;
  detected_description: string | null;
  detected_high_capacity: boolean | null;
  detected_at: string | null;
  current_level_percent: number | null;
  level_checked_at: string | null;
};
type RowsByColor = Record<CartridgeColor, Row>;

const DEFAULT_WARNING_THRESHOLD_PERCENT = 15;

function emptyRow(): Row {
  return {
    model: "",
    cost: "",
    yield_pages: "",
    warning_threshold_percent: String(DEFAULT_WARNING_THRESHOLD_PERCENT),
    detected_description: null,
    detected_high_capacity: null,
    detected_at: null,
    current_level_percent: null,
    level_checked_at: null,
  };
}

function rowFromCartridge(cartridge: Cartridge): Row {
  return {
    model: cartridge.model ?? "",
    cost: String(cartridge.cost),
    yield_pages: String(cartridge.yield_pages),
    warning_threshold_percent: String(cartridge.warning_threshold_percent),
    detected_description: cartridge.detected_description,
    detected_high_capacity: cartridge.detected_high_capacity,
    detected_at: cartridge.detected_at,
    current_level_percent: cartridge.current_level_percent,
    level_checked_at: cartridge.level_checked_at,
  };
}

export function TonerCartridgesCard({
  printerId,
  colorSupported,
  duplexSupported = false,
}: {
  printerId: string;
  colorSupported: boolean;
  duplexSupported?: boolean;
}) {
  const isAdmin = useCurrentUser()?.role === "admin";
  const colors: CartridgeColor[] = colorSupported
    ? ["black", "cyan", "magenta", "yellow"]
    : ["black"];
  const [rows, setRows] = useState<RowsByColor | null>(null);
  // Paper price per sheet, from Settings > Insights. Failure is silent: the
  // toner figures are the point here and still render without it.
  const [paperPerSheet, setPaperPerSheet] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    getReportFormulaSettings()
      .then((settings) => {
        if (!cancelled) setPaperPerSheet(settings.cost_per_sheet_paper);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
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

  function updateField(
    color: CartridgeColor,
    field: "model" | "cost" | "yield_pages" | "warning_threshold_percent",
    value: string,
  ) {
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
          warning_threshold_percent:
            Number(rows[color].warning_threshold_percent) || DEFAULT_WARNING_THRESHOLD_PERCENT,
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
        so double-check it against the physical cartridge before trusting it blindly. Level %
        refreshes automatically in the background every 30 minutes for SNMP-enabled printers; the
        Level badge turns red once it drops below that color&apos;s Warn Below threshold.
      </p>

      {rows === null && <Spinner label="Loading cartridges…" />}

      {rows !== null && (
        <CostSummary
          rows={rows}
          paperPerSheet={paperPerSheet}
          duplexSupported={duplexSupported}
        />
      )}

      {rows !== null && (
        <div className="flex flex-col gap-3">
          {colors.map((color) => (
            <div key={color} className="flex flex-col gap-1 border-t border-black/[.08] pt-3 first:border-t-0 first:pt-0 dark:border-white/[.1]">
              <div className="grid grid-cols-[5rem_1fr_1fr_1fr_1fr] items-end gap-3">
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
                <label className="flex flex-col gap-1 text-xs text-zinc-600 dark:text-zinc-400">
                  Warn Below (%)
                  <Input
                    type="number"
                    min="0"
                    max="100"
                    disabled={!isAdmin}
                    value={rows[color].warning_threshold_percent}
                    onChange={(e) => updateField(color, "warning_threshold_percent", e.target.value)}
                  />
                </label>
              </div>
              {(rows[color].detected_description || rows[color].current_level_percent !== null) && (
                <div className="flex flex-wrap items-center gap-2 pl-[calc(5rem+0.75rem)] text-xs text-zinc-500">
                  {rows[color].current_level_percent !== null && (
                    <>
                      <Badge
                        tone={
                          rows[color].current_level_percent! <
                          (Number(rows[color].warning_threshold_percent) ||
                            DEFAULT_WARNING_THRESHOLD_PERCENT)
                            ? "danger"
                            : "success"
                        }
                      >
                        Level: {rows[color].current_level_percent}%
                      </Badge>
                      <span className="text-zinc-400">
                        ({formatRelativeTime(rows[color].level_checked_at)})
                      </span>
                    </>
                  )}
                  {rows[color].detected_description && (
                    <span>Detected: {rows[color].detected_description}</span>
                  )}
                  {rows[color].detected_high_capacity && (
                    <Badge tone="info">High Capacity</Badge>
                  )}
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
              <li key={i}>
                {supply.description}
                {supply.level_percent !== null && ` (${supply.level_percent}%)`}
              </li>
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


function CostSummary({
  rows,
  paperPerSheet,
  duplexSupported,
}: {
  rows: Record<CartridgeColor, Row>;
  paperPerSheet: number | null;
  duplexSupported: boolean;
}) {
  const { mono, color } = tonerCostPerPage(rows);

  if (mono === null && color === null) {
    return (
      <p className="mb-3 rounded-lg border border-dashed border-black/[.12] p-3 text-sm text-zinc-500 dark:border-white/[.15]">
        Enter a cartridge cost and yield below to see what a page costs on this
        printer.
      </p>
    );
  }

  const paper = paperPerSheet ?? 0;
  // A duplex sheet carries two pages of toner on one sheet of paper, which is
  // the whole economics of duplex: it halves the paper and changes the toner
  // not at all.
  const sheet = (perPage: number, sides: number) => perPage * sides + paper;

  const kinds: [string, number | null][] = [
    ["Mono", mono],
    ["Color", color],
  ];

  return (
    <div className="mb-3 overflow-x-auto rounded-lg border border-black/[.08] p-3 dark:border-white/[.145]">
      <h4 className="text-sm font-medium text-black dark:text-zinc-50">
        Approximate cost per sheet
      </h4>

      <table className="mt-2 w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-zinc-500">
            <th className="pb-1 pr-4 font-normal" />
            <th className="pb-1 pr-4 font-normal">Toner / page</th>
            <th className="pb-1 pr-4 font-normal">Simplex</th>
            {duplexSupported ? (
              <th className="pb-1 pr-4 font-normal">Duplex</th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {kinds.map(([label, perPage]) => (
            <tr
              key={label}
              className="border-t border-black/[.06] dark:border-white/[.1]"
            >
              <td className="py-1.5 pr-4 text-zinc-600 dark:text-zinc-400">
                {label}
              </td>
              {perPage === null ? (
                <td
                  className="py-1.5 pr-4 text-zinc-500"
                  colSpan={duplexSupported ? 3 : 2}
                >
                  {label === "Color"
                    ? "Price a color cartridge to see this"
                    : "Price the black cartridge to see this"}
                </td>
              ) : (
                <>
                  <td className="py-1.5 pr-4 text-zinc-600 dark:text-zinc-400">
                    {money(perPage)}
                  </td>
                  <td className="py-1.5 pr-4 font-semibold text-black dark:text-zinc-50">
                    {money(sheet(perPage, 1))}
                  </td>
                  {duplexSupported ? (
                    <td className="py-1.5 pr-4 font-semibold text-black dark:text-zinc-50">
                      {money(sheet(perPage, 2))}
                      <span className="ml-1 text-xs font-normal text-zinc-500">
                        2 pages
                      </span>
                    </td>
                  ) : null}
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>

      <p className="mt-2 text-xs text-zinc-500">
        {paperPerSheet !== null
          ? `Sheet prices include ${money(paper)} of paper. `
          : "Paper is not included. "}
        {duplexSupported
          ? "Duplex halves the paper and changes the toner not at all — two pages of toner on one sheet. "
          : ""}
        Toner is priced from cartridge cost divided by rated yield, which is
        quoted against a standard test page of about 5% coverage per colorant
        (ISO/IEC 19752 and 19798). It is an average: a dense page costs several
        times this, a mostly-blank page a fraction.
      </p>
    </div>
  );
}
