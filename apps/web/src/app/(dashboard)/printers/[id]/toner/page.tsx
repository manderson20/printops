"use client";

import { usePrinterDetail } from "../PrinterDetailContext";
import { TonerCartridgesCard } from "../TonerCartridges";
import { TonerLevelHistoryCard } from "../TonerLevelHistory";

export default function TonerTab() {
  const { printer } = usePrinterDetail();
  const caps = printer.capabilities;
  const colorSupported = !!caps?.color_supported;
  // No duplex column for a printer that cannot duplex — quoting a price
  // for something the machine will not do is worse than omitting it.
  const duplexSupported = !!caps?.duplex_supported;

  return (
    <div className="flex flex-col gap-6">
      <TonerCartridgesCard
        printerId={printer.id}
        colorSupported={colorSupported}
        duplexSupported={duplexSupported}
      />
      <TonerLevelHistoryCard printerId={printer.id} colorSupported={colorSupported} />
    </div>
  );
}
