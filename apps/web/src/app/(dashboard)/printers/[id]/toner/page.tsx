"use client";

import { usePrinterDetail } from "../PrinterDetailContext";
import { TonerCartridgesCard } from "../TonerCartridges";
import { TonerLevelHistoryCard } from "../TonerLevelHistory";

export default function TonerTab() {
  const { printer } = usePrinterDetail();
  const caps = printer.capabilities;
  const colorSupported = !!caps?.color_supported;

  return (
    <div className="flex flex-col gap-6">
      <TonerCartridgesCard printerId={printer.id} colorSupported={colorSupported} />
      <TonerLevelHistoryCard printerId={printer.id} colorSupported={colorSupported} />
    </div>
  );
}
