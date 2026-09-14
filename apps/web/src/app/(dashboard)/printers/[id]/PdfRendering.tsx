"use client";

import { useState } from "react";
import { ApiError, updatePrinter, type Printer } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";

// The formats the print server can render a PDF into. scripts/lib/pdf_rendering.sh
// is the authority — it reads the queue's PPD, which CUPS builds from these same
// advertised formats — and refuses to leave a queue with nothing to print in.
// This only decides whether the checkbox is worth offering.
const RASTER_FORMATS = ["image/urf", "image/pwg-raster", "application/PCLm"];

export function PdfRenderingCard({
  printer,
  onUpdate,
}: {
  printer: Printer;
  onUpdate: (printer: Printer) => void;
}) {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const formats = printer.capabilities?.document_formats ?? [];
  // A printer that never accepted PDFs was never sent one, so there is nothing
  // to change — unless the setting is already on and needs a way back off.
  if (!isAdmin || (!formats.includes("application/pdf") && !printer.render_pdf_on_server)) {
    return null;
  }
  const canRender = formats.some((format) => RASTER_FORMATS.includes(format));
  // The API refuses the combination; see update_printer.
  const mediaColBroken = printer.capabilities?.media_col_broken === true;

  async function handleToggle() {
    setToggling(true);
    setError(null);
    try {
      const updated = await updatePrinter(printer.id, {
        render_pdf_on_server: !printer.render_pdf_on_server,
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
      <CardTitle className="mb-3">PDF Handling</CardTitle>
      <p className="mb-2 text-xs text-zinc-500">
        PDFs are normally sent to this printer as they are, and the printer draws the pages itself.
        Some printers fail on particular PDFs while printing others fine: they stop with a firmware
        error (on an HP LaserJet, a code starting 49), or report the job finished and print
        nothing.
      </p>
      <p className="mb-4 text-xs text-zinc-500">
        With this on, the print server draws every page and sends the printer finished images, so
        the printer never has to read the PDF. Long documents take longer to send. Changing it
        rebuilds this printer&apos;s queues.
      </p>
      <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
        <input
          type="checkbox"
          className="mt-1"
          checked={printer.render_pdf_on_server}
          disabled={toggling || ((!canRender || mediaColBroken) && !printer.render_pdf_on_server)}
          onChange={handleToggle}
        />
        <span>Render PDFs on the print server</span>
      </label>
      {!canRender && (
        <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
          This printer doesn&apos;t accept a page image format the server can render to, so its
          PDFs can only be sent as they are.
        </p>
      )}
      {canRender && mediaColBroken && (
        <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
          This printer can&apos;t be told which paper size a job needs, so its PDFs have to carry
          their own size, and pages rendered on the server would lose it.
        </p>
      )}
      {error && <ErrorState>{error}</ErrorState>}
    </Card>
  );
}
