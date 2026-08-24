import type { PrinterStatus } from "@/lib/api";

const PRINTER_STATUS: Record<
  PrinterStatus,
  { label: string; tone: "neutral" | "success" | "danger" }
> = {
  online: { label: "Online", tone: "success" },
  error: { label: "Error", tone: "danger" },
  offline: { label: "Offline", tone: "neutral" },
  unknown: { label: "Unknown", tone: "neutral" },
};

export function printerStatusInfo(status: PrinterStatus) {
  return PRINTER_STATUS[status] ?? { label: status, tone: "neutral" as const };
}

// Reason keywords that describe the network path rather than a fault on the
// printer. The printer is up and printing; it is reached unreliably. Shown in
// amber next to the real status rather than turning the printer red, because a
// red badge on a working machine is how people learn to stop reading the field.
export const NETWORK_UNSTABLE_REASON = "printops-network-unstable";

export function hasNetworkWarning(reasons: string[] | null | undefined) {
  return (reasons ?? []).includes(NETWORK_UNSTABLE_REASON);
}

// An offline printer with work piling up behind it that is *not* simply away
// for the night — it has never answered at its stored address, or has been
// gone more than a day. Shown in red rather than the neutral grey an offline
// printer normally gets, because those two look identical and only one of them
// needs somebody.
export const UNREACHABLE_WITH_JOBS_REASON = "printops-unreachable-with-jobs";

export function hasWaitingJobsWarning(reasons: string[] | null | undefined) {
  return (reasons ?? []).includes(UNREACHABLE_WITH_JOBS_REASON);
}
