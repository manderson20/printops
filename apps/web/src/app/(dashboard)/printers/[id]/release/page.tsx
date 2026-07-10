"use client";

import { PrintReleaseCard } from "../PrintRelease";
import { usePrinterDetail } from "../PrinterDetailContext";
import { QuotasCard } from "../Quotas";

export default function ReleaseAndQuotasTab() {
  const { printer, setPrinter } = usePrinterDetail();

  return (
    <div className="flex flex-col gap-6">
      <PrintReleaseCard printer={printer} onUpdate={setPrinter} />
      <QuotasCard printerId={printer.id} />
    </div>
  );
}
