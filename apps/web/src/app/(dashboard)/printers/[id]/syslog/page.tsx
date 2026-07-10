"use client";

import { usePrinterDetail } from "../PrinterDetailContext";
import { SyslogEventsCard } from "../SyslogEvents";

export default function SyslogTab() {
  const { printer } = usePrinterDetail();

  return <SyslogEventsCard printerId={printer.id} />;
}
