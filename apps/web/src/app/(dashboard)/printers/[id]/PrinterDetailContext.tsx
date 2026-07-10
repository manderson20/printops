"use client";

import { createContext, useContext } from "react";
import type { Printer } from "@/lib/api";

type PrinterDetailContextValue = {
  printer: Printer;
  setPrinter: (printer: Printer) => void;
};

// One fetch in the layout (PrinterDetailLayout), shared by every tab —
// avoids each tab re-fetching the same printer, and keeps an edit made on
// one tab (e.g. Overview's Details form) immediately visible on another
// (e.g. Toner's colorSupported check) without a refetch.
export const PrinterDetailContext = createContext<PrinterDetailContextValue | null>(null);

export function usePrinterDetail(): PrinterDetailContextValue {
  const ctx = useContext(PrinterDetailContext);
  if (!ctx) {
    throw new Error("usePrinterDetail must be used within a printer detail tab");
  }
  return ctx;
}
