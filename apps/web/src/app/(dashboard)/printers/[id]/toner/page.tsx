"use client";

import { usePrinterDetail } from "../PrinterDetailContext";
import { TonerCartridgesCard } from "../TonerCartridges";

export default function TonerTab() {
  const { printer, setPrinter } = usePrinterDetail();
  const caps = printer.capabilities;

  return (
    <TonerCartridgesCard
      printer={printer}
      colorSupported={!!caps?.color_supported}
      onUpdate={setPrinter}
    />
  );
}
