import type { JobStatus } from "@/lib/api";

const JOB_STATUS: Record<JobStatus, { label: string; tone: "neutral" | "info" | "success" | "danger" }> = {
  received: { label: "Received", tone: "neutral" },
  forwarding: { label: "Forwarding", tone: "info" },
  forwarded: { label: "Forwarded", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

export function jobStatusInfo(status: JobStatus) {
  return JOB_STATUS[status] ?? { label: status, tone: "neutral" as const };
}
