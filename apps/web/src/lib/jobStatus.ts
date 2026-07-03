import type { AttributionMethod, JobStatus } from "@/lib/api";

const JOB_STATUS: Record<JobStatus, { label: string; tone: "neutral" | "info" | "success" | "danger" }> = {
  received: { label: "Received", tone: "neutral" },
  forwarding: { label: "Forwarding", tone: "info" },
  forwarded: { label: "Forwarded", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export function jobStatusInfo(status: JobStatus) {
  return JOB_STATUS[status] ?? { label: status, tone: "neutral" as const };
}

const ATTRIBUTION_METHOD: Record<AttributionMethod, { label: string; tone: "neutral" | "info" | "warning" }> = {
  cups: { label: "CUPS", tone: "neutral" },
  mosyle: { label: "Mosyle", tone: "info" },
  google_workspace: { label: "Google Workspace", tone: "info" },
  unresolved: { label: "Unresolved", tone: "warning" },
};

export function attributionMethodInfo(method: AttributionMethod) {
  return ATTRIBUTION_METHOD[method] ?? { label: method, tone: "neutral" as const };
}
