import type { PrinterStatus } from "@/lib/api";

const PRINTER_STATUS: Record<PrinterStatus, { label: string; tone: "neutral" | "success" | "danger" }> = {
  online: { label: "Online", tone: "success" },
  error: { label: "Error", tone: "danger" },
  offline: { label: "Offline", tone: "neutral" },
  unknown: { label: "Unknown", tone: "neutral" },
};

export function printerStatusInfo(status: PrinterStatus) {
  return PRINTER_STATUS[status] ?? { label: status, tone: "neutral" as const };
}
