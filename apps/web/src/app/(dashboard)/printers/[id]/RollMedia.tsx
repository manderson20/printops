"use client";

import { useState } from "react";
import { ApiError, updatePrinter, type Printer } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";

// This card only renders for printers whose probed capabilities advertise the
// IPP "trim" finishing — i.e. a roll-fed printer with a cutter (e.g. the Canon
// TM-300 plotter). That gate is what makes the feature self-provisioning: any
// newly-added printer that reports a cutter surfaces this toggle automatically,
// with no per-model configuration. See app/printers/capabilities.py (server
// side) for where "trim" comes from.
export function supportsRollCut(printer: Printer): boolean {
  return printer.capabilities?.finishings?.includes("trim") ?? false;
}

export function RollMediaCard({
  printer,
  onUpdate,
}: {
  printer: Printer;
  onUpdate: (printer: Printer) => void;
}) {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isAdmin || !supportsRollCut(printer)) return null;

  async function handleToggle() {
    setToggling(true);
    setError(null);
    try {
      const updated = await updatePrinter(printer.id, {
        roll_autocut: !printer.roll_autocut,
      });
      onUpdate(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update printer");
    } finally {
      setToggling(false);
    }
  }

  return (
    <Card>
      <CardTitle className="mb-3">Roll Media</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        This printer reports a roll cutter. With auto-cut on, the roll is trimmed after each job
        so every print comes out as a separate sheet. Turn it off to leave jobs joined on the roll
        (e.g. to gang several drawings together and cut them by hand).
      </p>
      <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
        <input
          type="checkbox"
          className="mt-1"
          checked={printer.roll_autocut}
          disabled={toggling}
          onChange={handleToggle}
        />
        <span>Auto-cut the roll after each job</span>
      </label>
      {error && <ErrorState>{error}</ErrorState>}
    </Card>
  );
}
