"use client";

import { CopierCard } from "../Copier";
import { usePrinterDetail } from "../PrinterDetailContext";

export default function CopierTab() {
  const { printer, setPrinter } = usePrinterDetail();

  return (
    <div className="flex flex-col gap-6">
      <CopierCard printer={printer} onUpdate={setPrinter} />
    </div>
  );
}
