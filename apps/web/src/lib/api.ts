import { getToken } from "@/lib/auth";
import { API_URL } from "@/lib/config";

export { API_URL };

export type HealthStatus = {
  status: string;
  service: string;
};

export async function getHealth(): Promise<HealthStatus> {
  const response = await fetch(`${API_URL}/healthz`);
  if (!response.ok) {
    throw new Error(`API health check failed: ${response.status}`);
  }
  return response.json();
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function authorizedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.detail ?? `Request failed: ${response.status}`);
  }
  return response;
}

export type Capabilities = {
  make_model: string | null;
  firmware_version: string | null;
  duplex_supported: boolean;
  color_supported: boolean;
  copies_max: number | null;
  resolutions: { x: number; y: number; unit: number | null }[];
  media_sizes: string[];
  media_sources: string[];
  media_types: string[];
  output_bins: string[];
  finishings: string[];
  collation_supported: boolean;
  pin_printing_supported: boolean;
  accounting_supported: boolean;
};

export type Printer = {
  id: string;
  name: string;
  ip_address: string;
  port: number;
  use_tls: boolean;
  ipp_path: string | null;
  airprint_enabled: boolean;
  manufacturer: string | null;
  model: string | null;
  hostname: string | null;
  serial_number: string | null;
  building: string | null;
  room: string | null;
  department: string | null;
  notes: string | null;
  capabilities: Capabilities | null;
  capabilities_detected_at: string | null;
  capabilities_error: string | null;
  queue_sync_error: string | null;
  created_at: string;
  updated_at: string;
};

export type PrinterCreateInput = {
  name: string;
  ip_address: string;
  port?: number;
  use_tls?: boolean;
  ipp_path?: string | null;
  airprint_enabled?: boolean;
  manufacturer?: string | null;
  model?: string | null;
  hostname?: string | null;
  serial_number?: string | null;
  building?: string | null;
  room?: string | null;
  department?: string | null;
  notes?: string | null;
};

export type PrinterUpdateInput = Partial<PrinterCreateInput>;

export async function listPrinters(): Promise<Printer[]> {
  const response = await authorizedFetch("/api/v1/printers");
  return response.json();
}

export async function getPrinter(id: string): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}`);
  return response.json();
}

export async function createPrinter(input: PrinterCreateInput): Promise<Printer> {
  const response = await authorizedFetch("/api/v1/printers", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function updatePrinter(id: string, input: PrinterUpdateInput): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function deletePrinter(id: string): Promise<void> {
  await authorizedFetch(`/api/v1/printers/${id}`, { method: "DELETE" });
}

export async function rediscoverPrinter(id: string): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/discover`, { method: "POST" });
  return response.json();
}

export async function resyncQueue(id: string): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/resync-queue`, { method: "POST" });
  return response.json();
}

export async function testPrintPrinter(id: string): Promise<{ message: string }> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/test-print`, { method: "POST" });
  return response.json();
}

export type MdmConnectionInfo = {
  queue_name: string;
  host: string;
  port: number;
  resource_path: string;
  ipp_uri: string;
  airprint_enabled: boolean;
};

export async function getMdmConnection(id: string): Promise<MdmConnectionInfo> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/mdm-connection`);
  return response.json();
}

export type JobStatus = "received" | "forwarding" | "forwarded" | "failed";

export type Job = {
  id: string;
  printer_id: string;
  printer_name: string;
  cups_job_id: number | null;
  submitted_by: string | null;
  file_size_bytes: number | null;
  status: JobStatus;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export async function listJobs(params?: { printer_id?: string; limit?: number }): Promise<Job[]> {
  const query = new URLSearchParams();
  if (params?.printer_id) query.set("printer_id", params.printer_id);
  if (params?.limit) query.set("limit", String(params.limit));
  const qs = query.toString();
  const response = await authorizedFetch(`/api/v1/jobs${qs ? `?${qs}` : ""}`);
  return response.json();
}
