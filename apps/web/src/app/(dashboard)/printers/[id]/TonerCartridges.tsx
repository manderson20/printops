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

/** What a page costs in toner — mirroring compute_printer_rate() exactly.
 *
 * This panel previews a figure the server computes, so it has to agree with it
 * in every case, not just the ordinary one. The first version reimplemented
 * the rule from its description and diverged three ways: it required a
 * non-zero cost, it summed however many colour cartridges were priced, and it
 * indexed colour rows that do not exist on a mono printer. The last of those
 * crashed the tab outright.
 *
 * The rule, from app/reports/formulas.py:
 *   - configured means `yield_pages > 0`. Cost is not part of the test, so a
 *     bundled cartridge priced at zero is configured and rates at $0.0000.
 *   - mono prices off Black alone, or the flat rate if Black is unconfigured.
 *   - colour prices off all four summed, and falls back to the flat rate
 *     unless *every* slot is configured — so a half-finished setup does not
 *     quietly report a fraction of the real colour cost.
 */
type Rate = { perPage: number; fromCartridges: boolean };

function tonerCostPerPage(
  rows: Partial<Record<CartridgeColor, Row>>,
  fallbackMono: number,
  fallbackColor: number,
): { mono: Rate; color: Rate } {
  const configured = (color: CartridgeColor): number | null => {
    const row = rows[color];
    // Absent entirely on a mono printer, where only the black row is built.
    if (!row) return null;
    const cost = Number(row.cost);
    const yieldPages = Number(row.yield_pages);
    if (!Number.isFinite(cost) || !Number.isFinite(yieldPages)) return null;
    if (yieldPages <= 0) return null;
    return cost / yieldPages;
  };

  const black = configured("black");
  const all = (["black", "cyan", "magenta", "yellow"] as CartridgeColor[]).map(configured);

  return {
    mono:
      black === null
        ? { perPage: fallbackMono, fromCartridges: false }
        : { perPage: black, fromCartridges: true },
    color: all.every((value) => value !== null)
      ? {
          perPage: (all as number[]).reduce((sum, value) => sum + value, 0),
          fromCartridges: true,
        }
      : { perPage: fallbackColor, fromCartridges: false },
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
  const [fallbackMono, setFallbackMono] = useState<number | null>(null);
  const [fallbackColor, setFallbackColor] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    getReportFormulaSettings()
      .then((settings) => {
        if (cancelled) return;
        setPaperPerSheet(settings.cost_per_sheet_paper);
        setFallbackMono(settings.cost_per_page_mono);
        setFallbackColor(settings.cost_per_page_color);
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
          colorSupported={colorSupported}
          fallbackMono={fallbackMono}
          fallbackColor={fallbackColor}
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
  colorSupported,
  fallbackMono,
  fallbackColor,
}: {
  rows: Partial<Record<CartridgeColor, Row>>;
  paperPerSheet: number | null;
  duplexSupported: boolean;
  colorSupported: boolean;
  fallbackMono: number | null;
  fallbackColor: number | null;
}) {
  // Until the flat rates load there is no honest figure to show: a cartridge
  // that is not configured prices off them, and guessing zero would put a
  // confidently wrong number on screen.
  if (fallbackMono === null || fallbackColor === null) return null;

  const { mono, color } = tonerCostPerPage(rows, fallbackMono, fallbackColor);
  const paper = paperPerSheet ?? 0;
  // A duplex sheet carries two pages of toner on one sheet of paper, which is
  // the whole economics of duplex: it halves the paper and changes the toner
  // not at all.
  const sheet = (perPage: number, sides: number) => perPage * sides + paper;

  const kinds: [string, Rate][] = colorSupported
    ? [
        ["Mono", mono],
        ["Color", color],
      ]
    : [["Mono", mono]];

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
          {kinds.map(([label, rate]) => (
            <tr
              key={label}
              className="border-t border-black/[.06] dark:border-white/[.1]"
            >
              <td className="py-1.5 pr-4 text-zinc-600 dark:text-zinc-400">
                {label}
                {!rate.fromCartridges ? (
                  <span
                    className="ml-1 text-xs text-zinc-500"
                    title={
                      label === "Color"
                        ? "All four cartridges must have a yield before color prices off them; until then the flat rate from Settings > Insights applies."
                        : "The black cartridge has no yield yet, so the flat rate from Settings > Insights applies."
                    }
                  >
                    flat rate
                  </span>
                ) : null}
              </td>
              <td className="py-1.5 pr-4 text-zinc-600 dark:text-zinc-400">
                {money(rate.perPage)}
              </td>
              <td className="py-1.5 pr-4 font-semibold text-black dark:text-zinc-50">
                {money(sheet(rate.perPage, 1))}
              </td>
              {duplexSupported ? (
                <td className="py-1.5 pr-4 font-semibold text-black dark:text-zinc-50">
                  {money(sheet(rate.perPage, 2))}
                  <span className="ml-1 text-xs font-normal text-zinc-500">
                    2 pages
                  </span>
                </td>
              ) : null}
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
        A cartridge counts as configured once it has a yield, whatever its cost.
        Toner is priced from cartridge cost divided by rated yield, quoted
        against a standard test page of about 5% coverage per colorant (ISO/IEC
        19752 and 19798) — an average, not what any particular page costs.
      </p>
    </div>
  );
}
