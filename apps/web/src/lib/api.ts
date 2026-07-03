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
export type AttributionMethod = "cups" | "mosyle" | "google_workspace" | "unresolved";

export type Job = {
  id: string;
  printer_id: string;
  printer_name: string;
  cups_job_id: number | null;
  submitted_by: string | null;
  attribution_method: AttributionMethod;
  file_size_bytes: number | null;
  page_count: number | null;
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

export type UserUsage = {
  submitted_by: string | null;
  job_count: number;
  total_pages: number;
  total_bytes: number;
};

export async function getJobUsage(): Promise<UserUsage[]> {
  const response = await authorizedFetch("/api/v1/jobs/usage");
  return response.json();
}

export type MosyleSettings = {
  base_url: string;
  admin_email: string | null;
  has_access_token: boolean;
  has_admin_password: boolean;
  enabled: boolean;
  last_synced_at: string | null;
  last_sync_error: string | null;
  device_count: number;
};

export type MosyleSettingsInput = {
  base_url?: string;
  access_token?: string;
  admin_email?: string;
  admin_password?: string;
  enabled?: boolean;
};

export type MosyleTestResult = {
  ok: boolean;
  device_count: number | null;
  error: string | null;
};

export async function getMosyleSettings(): Promise<MosyleSettings> {
  const response = await authorizedFetch("/api/v1/settings/mosyle");
  return response.json();
}

export async function updateMosyleSettings(input: MosyleSettingsInput): Promise<MosyleSettings> {
  const response = await authorizedFetch("/api/v1/settings/mosyle", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function testMosyleConnection(input: MosyleSettingsInput): Promise<MosyleTestResult> {
  const response = await authorizedFetch("/api/v1/settings/mosyle/test", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function syncMosyleDevices(): Promise<MosyleSettings> {
  const response = await authorizedFetch("/api/v1/settings/mosyle/sync", { method: "POST" });
  return response.json();
}

export type ClassGuardSettings = {
  base_url: string;
  has_access_token: boolean;
  enabled: boolean;
  last_test_at: string | null;
  last_test_error: string | null;
};

export type ClassGuardSettingsInput = {
  base_url?: string;
  access_token?: string;
  enabled?: boolean;
};

export type ClassGuardTestResult = {
  ok: boolean;
  mac_address: string | null;
  error: string | null;
};

export async function getClassGuardSettings(): Promise<ClassGuardSettings> {
  const response = await authorizedFetch("/api/v1/settings/classguard");
  return response.json();
}

export async function updateClassGuardSettings(
  input: ClassGuardSettingsInput,
): Promise<ClassGuardSettings> {
  const response = await authorizedFetch("/api/v1/settings/classguard", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function testClassGuardConnection(
  input: ClassGuardSettingsInput & { test_ip: string },
): Promise<ClassGuardTestResult> {
  const response = await authorizedFetch("/api/v1/settings/classguard/test", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export type GoogleWorkspaceSettings = {
  admin_email: string | null;
  customer_id: string;
  has_service_account_json: boolean;
  enabled: boolean;
  last_synced_at: string | null;
  last_sync_error: string | null;
  device_count: number;
};

export type GoogleWorkspaceSettingsInput = {
  service_account_json?: string;
  admin_email?: string;
  customer_id?: string;
  enabled?: boolean;
};

export type GoogleWorkspaceTestResult = {
  ok: boolean;
  device_count: number | null;
  error: string | null;
};

export async function getGoogleWorkspaceSettings(): Promise<GoogleWorkspaceSettings> {
  const response = await authorizedFetch("/api/v1/settings/google-workspace");
  return response.json();
}

export async function updateGoogleWorkspaceSettings(
  input: GoogleWorkspaceSettingsInput,
): Promise<GoogleWorkspaceSettings> {
  const response = await authorizedFetch("/api/v1/settings/google-workspace", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function testGoogleWorkspaceConnection(
  input: GoogleWorkspaceSettingsInput,
): Promise<GoogleWorkspaceTestResult> {
  const response = await authorizedFetch("/api/v1/settings/google-workspace/test", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function syncGoogleWorkspaceDevices(): Promise<GoogleWorkspaceSettings> {
  const response = await authorizedFetch("/api/v1/settings/google-workspace/sync", { method: "POST" });
  return response.json();
}

export type Role = "admin" | "viewer";

export type CurrentUser = {
  username: string;
  role: Role;
  email: string | null;
  name: string | null;
};

export async function getMe(): Promise<CurrentUser> {
  const response = await authorizedFetch("/auth/me");
  return response.json();
}

export type UserAccount = {
  id: string;
  email: string;
  name: string | null;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
};

export async function listUsers(): Promise<UserAccount[]> {
  const response = await authorizedFetch("/api/v1/users");
  return response.json();
}

export async function updateUser(
  id: string,
  input: { role?: Role; is_active?: boolean },
): Promise<UserAccount> {
  const response = await authorizedFetch(`/api/v1/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
  return response.json();
}

export type GoogleSsoSettings = {
  client_id: string | null;
  has_client_secret: boolean;
  workspace_domain: string | null;
  initial_admin_emails: string[];
  redirect_base_url: string | null;
  enabled: boolean;
};

export type GoogleSsoSettingsInput = {
  client_id?: string;
  client_secret?: string;
  workspace_domain?: string;
  initial_admin_emails?: string[];
  redirect_base_url?: string;
  enabled?: boolean;
};

export async function getGoogleSsoSettings(): Promise<GoogleSsoSettings> {
  const response = await authorizedFetch("/api/v1/settings/google-sso");
  return response.json();
}

export async function updateGoogleSsoSettings(
  input: GoogleSsoSettingsInput,
): Promise<GoogleSsoSettings> {
  const response = await authorizedFetch("/api/v1/settings/google-sso", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}
