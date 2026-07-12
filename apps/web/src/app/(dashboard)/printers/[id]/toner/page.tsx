"use client";

import { usePrinterDetail } from "../PrinterDetailContext";
import { TonerCartridgesCard } from "../TonerCartridges";

export default function TonerTab() {
  const { printer } = usePrinterDetail();
  const caps = printer.capabilities;

  return (
    <TonerCartridgesCard printerId={printer.id} colorSupported={!!caps?.color_supported} />
  );
}
