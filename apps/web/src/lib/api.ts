import { getToken, logout } from "@/lib/auth";
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
  if (response.status === 401 && getToken()) {
    // Only a signed-in session can get here (verify_backend_token, the CUPS
    // script's auth, never runs behind authorizedFetch) — so a 401 always
    // means the JWT expired or was invalidated, not a bad request. Bounce to
    // login immediately rather than surfacing "Could not validate
    // credentials" on whatever form happened to trigger the call.
    logout();
    window.location.href = "/login?expired=1";
  }
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
  status: PrinterStatus;
  status_reasons: string[] | null;
  status_message: string | null;
  status_checked_at: string | null;
  release_required: boolean;
  release_token: string | null;
  snmp_enabled: boolean;
  snmp_port: number | null;
  snmp_version: SnmpVersion | null;
  has_snmp_community: boolean;
  snmp_vendor_profile: VendorProfile | null;
  page_count_total: number | null;
  page_count_copy: number | null;
  page_count_print: number | null;
  page_count_confidence: PageCountConfidence | null;
  page_count_vendor_profile_used: string | null;
  page_count_checked_at: string | null;
  page_count_error: string | null;
  created_at: string;
  updated_at: string;
};

export type PrinterStatus = "online" | "error" | "offline" | "unknown";

export type SnmpVersion = "v1" | "v2c";
export type VendorProfile = "canon" | "konica_minolta" | "hp" | "lexmark" | "kyocera" | "generic";
export type PageCountConfidence = "verified" | "best_effort" | "unsupported";

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

export type PrinterUpdateInput = Partial<PrinterCreateInput> & {
  release_required?: boolean;
  snmp_enabled?: boolean;
  snmp_port?: number | null;
  // "" clears the override back to the global default (see
  // app/routers/printers.py:update_printer) — not just null/omitted.
  snmp_version?: SnmpVersion | "" | null;
  snmp_community?: string | null;
  snmp_vendor_profile?: VendorProfile | "" | null;
};

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

export async function regeneratePrinterReleaseToken(id: string): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/regenerate-release-token`, {
    method: "POST",
  });
  return response.json();
}

export async function testPrintPrinter(id: string): Promise<{ message: string }> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/test-print`, { method: "POST" });
  return response.json();
}

export async function checkPrinterStatus(id: string): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/check-status`, { method: "POST" });
  return response.json();
}

export async function checkPrinterCounters(id: string): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/check-counters`, {
    method: "POST",
  });
  return response.json();
}

export type DailyCounterDelta = {
  bucket_start: string;
  total_delta: number | null;
  copy_delta: number | null;
  print_delta: number | null;
};

export async function getPrinterCounterHistory(
  id: string,
  days: number,
): Promise<DailyCounterDelta[]> {
  const response = await authorizedFetch(
    `/api/v1/printers/${id}/counter-history?days=${days}`,
  );
  return response.json();
}

export async function purgePrinterJobs(id: string): Promise<{ cancelled_count: number }> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/purge-jobs`, { method: "POST" });
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

export type JobStatus = "received" | "forwarding" | "forwarded" | "failed" | "cancelled";
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
  document_name: string | null;
  copy_count: number | null;
  color_mode: "color" | "monochrome" | null;
  duplex: boolean | null;
  paper_size: string | null;
  source: string;
  completed_at: string | null;
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
  email: string | null;
  name: string | null;
  is_other: boolean;
  job_count: number;
  total_pages: number;
  total_bytes: number;
};

export async function getJobUsage(): Promise<UserUsage[]> {
  const response = await authorizedFetch("/api/v1/jobs/usage");
  return response.json();
}

export async function cancelJob(id: string): Promise<Job> {
  const response = await authorizedFetch(`/api/v1/jobs/${id}/cancel`, { method: "POST" });
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
  staff_org_unit_path: string | null;
};

export type GoogleWorkspaceSettingsInput = {
  service_account_json?: string;
  admin_email?: string;
  customer_id?: string;
  enabled?: boolean;
  staff_org_unit_path?: string;
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

export type KnownDevice = {
  mac_address: string;
  source: "mosyle" | "google_workspace";
  serial_number: string | null;
  device_name: string | null;
  reported_email: string | null;
  reported_username: string | null;
  override_email: string | null;
  override_note: string | null;
};

export async function listKnownDevices(): Promise<KnownDevice[]> {
  const response = await authorizedFetch("/api/v1/devices");
  return response.json();
}

export type DeviceOverride = {
  mac_address: string;
  resolved_email: string;
  note: string | null;
  created_at: string;
  updated_at: string;
  backfilled_job_count: number;
};

export async function setDeviceOverride(
  macAddress: string,
  input: { resolved_email: string; note?: string | null },
): Promise<DeviceOverride> {
  const response = await authorizedFetch(`/api/v1/devices/${encodeURIComponent(macAddress)}/override`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function deleteDeviceOverride(macAddress: string): Promise<void> {
  await authorizedFetch(`/api/v1/devices/${encodeURIComponent(macAddress)}/override`, {
    method: "DELETE",
  });
}

export type GoogleWorkspaceUserEntry = {
  email: string;
  name: string | null;
  employee_id: string | null;
};

export async function listGoogleWorkspaceUsers(): Promise<GoogleWorkspaceUserEntry[]> {
  const response = await authorizedFetch("/api/v1/settings/google-workspace/users");
  return response.json();
}

/** Downloads a CSV of Name/Email/PIN (PIN = Google Workspace Employee ID)
 * for loading into a copier's local PIN list — see
 * app/routers/settings.py:export_copier_pin_roster for the caveat that the
 * column layout is a starting point, not a confirmed match for any
 * specific device's bulk-import format. */
export async function downloadCopierPinRoster(): Promise<void> {
  const response = await authorizedFetch("/api/v1/settings/google-workspace/copier-pin-roster.csv");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "copier-pin-roster.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export async function getVersion(): Promise<string> {
  const response = await authorizedFetch("/api/v1/updates/version");
  const body: { version: string } = await response.json();
  return body.version;
}

export type UpdateCheck = {
  current_version: string;
  latest_version: string;
  update_available: boolean;
  changelog: string | null;
};

export async function checkForUpdate(): Promise<UpdateCheck> {
  const response = await authorizedFetch("/api/v1/updates/check");
  return response.json();
}

export type UpdateScheduleEntry = {
  id: string;
  target_version: string;
  scheduled_at: string;
  status: "pending" | "in_progress" | "completed" | "failed";
  log: string | null;
  requested_by: string | null;
  completed_at: string | null;
  created_at: string;
};

export async function listUpdateSchedule(): Promise<UpdateScheduleEntry[]> {
  const response = await authorizedFetch("/api/v1/updates/schedule");
  return response.json();
}

export async function scheduleUpdate(input: {
  scheduled_at: string;
  target_version: string;
}): Promise<UpdateScheduleEntry> {
  const response = await authorizedFetch("/api/v1/updates/schedule", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function cancelScheduledUpdate(id: string): Promise<void> {
  await authorizedFetch(`/api/v1/updates/schedule/${id}`, { method: "DELETE" });
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

// --- Print Insights ---

export type ReportFilters = {
  start?: string;
  end?: string;
  building?: string;
  department?: string;
  printer_id?: string;
  submitted_by?: string;
  status?: string;
  color_mode?: string;
  duplex?: boolean;
};

function buildReportQuery(filters?: ReportFilters, extra?: Record<string, string>): string {
  const query = new URLSearchParams();
  if (filters?.start) query.set("start", filters.start);
  if (filters?.end) query.set("end", filters.end);
  if (filters?.building) query.set("building", filters.building);
  if (filters?.department) query.set("department", filters.department);
  if (filters?.printer_id) query.set("printer_id", filters.printer_id);
  if (filters?.submitted_by) query.set("submitted_by", filters.submitted_by);
  if (filters?.status) query.set("status", filters.status);
  if (filters?.color_mode) query.set("color_mode", filters.color_mode);
  if (filters?.duplex !== undefined) query.set("duplex", String(filters.duplex));
  if (extra) {
    for (const [key, value] of Object.entries(extra)) query.set(key, value);
  }
  return query.toString();
}

export type ReportSummary = {
  total_jobs: number;
  forwarded_jobs: number;
  failed_jobs: number;
  cancelled_jobs: number;
  total_pages: number;
  color_pages: number;
  mono_pages: number;
  unknown_color_mode_pages: number;
  duplex_pages: number;
  simplex_pages: number;
  unknown_duplex_pages: number;
  estimated_cost_mono: number;
  estimated_cost_color: number;
  estimated_cost_paper: number;
  estimated_cost_total: number;
  sheets_of_paper: number;
  duplex_sheets_saved: number;
  trees_used: number;
  co2_grams: number;
};

export async function getReportSummary(filters?: ReportFilters): Promise<ReportSummary> {
  const qs = buildReportQuery(filters);
  const response = await authorizedFetch(`/api/v1/reports/summary${qs ? `?${qs}` : ""}`);
  return response.json();
}

export type ReportGranularity = "day" | "week" | "month";

export type TimelineBucket = {
  bucket_start: string;
  total_pages: number;
  color_pages: number;
  mono_pages: number;
  duplex_pages: number;
  simplex_pages: number;
  job_count: number;
};

export async function getReportTimeline(
  granularity: ReportGranularity,
  filters?: ReportFilters,
): Promise<TimelineBucket[]> {
  const qs = buildReportQuery(filters, { granularity });
  const response = await authorizedFetch(`/api/v1/reports/timeline${qs ? `?${qs}` : ""}`);
  return response.json();
}

export type LeaderboardEntry = {
  key: string;
  label: string;
  job_count: number;
  total_pages: number;
};

export async function getReportLeaderboard(
  type: "printer" | "user",
  filters?: ReportFilters,
  limit = 10,
): Promise<LeaderboardEntry[]> {
  const qs = buildReportQuery(filters, { type, limit: String(limit) });
  const response = await authorizedFetch(`/api/v1/reports/leaderboard${qs ? `?${qs}` : ""}`);
  return response.json();
}

export type CostEntry = {
  key: string;
  label: string;
  job_count: number;
  page_count: number;
  toner_cost: number;
  paper_cost: number;
  total_cost: number;
};

export async function getCostBreakdown(
  groupBy: "printer" | "user",
  filters?: ReportFilters,
): Promise<CostEntry[]> {
  const qs = buildReportQuery(filters, { group_by: groupBy });
  const response = await authorizedFetch(`/api/v1/reports/cost-breakdown${qs ? `?${qs}` : ""}`);
  return response.json();
}

export type PeakTimes = {
  by_day_of_week: Record<string, number>;
  by_hour: Record<string, number>;
};

export async function getReportPeakTimes(filters?: ReportFilters): Promise<PeakTimes> {
  const qs = buildReportQuery(filters);
  const response = await authorizedFetch(`/api/v1/reports/peak-times${qs ? `?${qs}` : ""}`);
  return response.json();
}

export async function getReportFunFacts(
  periodLabel: string,
  filters?: ReportFilters,
): Promise<string[]> {
  const qs = buildReportQuery(filters, { period_label: periodLabel });
  const response = await authorizedFetch(`/api/v1/reports/fun-facts${qs ? `?${qs}` : ""}`);
  const body: { facts: string[] } = await response.json();
  return body.facts;
}

/** Downloads the filtered job export as a CSV file — routed through
 * authorizedFetch (unlike a plain <a href>) so the JWT bearer token
 * actually reaches the API; browsers don't attach custom headers to a
 * navigation click. */
export async function downloadReportCsv(filters?: ReportFilters): Promise<void> {
  const qs = buildReportQuery(filters);
  const response = await authorizedFetch(`/api/v1/reports/export.csv${qs ? `?${qs}` : ""}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "print-insights-export.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export type ReportSnapshotFilters = {
  building?: string | null;
  department?: string | null;
  printer_id?: string | null;
  submitted_by?: string | null;
  status?: string | null;
  color_mode?: string | null;
  duplex?: boolean | null;
};

export type ReportSnapshot = {
  id: string;
  name: string;
  range_start: string;
  range_end: string;
  filters: ReportSnapshotFilters;
  totals: ReportSummary;
  fun_facts: string[];
  created_by: string;
  created_at: string;
};

export async function listReportSnapshots(): Promise<ReportSnapshot[]> {
  const response = await authorizedFetch("/api/v1/reports/snapshots");
  return response.json();
}

export async function createReportSnapshot(input: {
  name: string;
  range_start: string;
  range_end: string;
  filters?: ReportSnapshotFilters;
  period_label?: string;
}): Promise<ReportSnapshot> {
  const response = await authorizedFetch("/api/v1/reports/snapshots", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function getReportSnapshot(id: string): Promise<ReportSnapshot> {
  const response = await authorizedFetch(`/api/v1/reports/snapshots/${id}`);
  return response.json();
}

export async function deleteReportSnapshot(id: string): Promise<void> {
  await authorizedFetch(`/api/v1/reports/snapshots/${id}`, { method: "DELETE" });
}

export type ReportFormulaSettings = {
  cost_per_page_mono: number;
  cost_per_page_color: number;
  sheets_per_tree: number;
  co2_grams_per_sheet: number;
  cost_per_sheet_paper: number;
};

export type ReportFormulaSettingsInput = Partial<ReportFormulaSettings>;

export async function getReportFormulaSettings(): Promise<ReportFormulaSettings> {
  const response = await authorizedFetch("/api/v1/settings/report-formulas");
  return response.json();
}

export async function updateReportFormulaSettings(
  input: ReportFormulaSettingsInput,
): Promise<ReportFormulaSettings> {
  const response = await authorizedFetch("/api/v1/settings/report-formulas", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export type CartridgeColor = "black" | "cyan" | "magenta" | "yellow";

export type Cartridge = {
  color: CartridgeColor;
  cost: number;
  yield_pages: number;
};

export async function getPrinterCartridges(printerId: string): Promise<Cartridge[]> {
  const response = await authorizedFetch(`/api/v1/printers/${printerId}/toner-cartridges`);
  return response.json();
}

export async function updatePrinterCartridges(
  printerId: string,
  cartridges: Cartridge[],
): Promise<Cartridge[]> {
  const response = await authorizedFetch(`/api/v1/printers/${printerId}/toner-cartridges`, {
    method: "PUT",
    body: JSON.stringify(cartridges),
  });
  return response.json();
}

export type PrintReleaseSettings = {
  hold_expiry_hours: number;
};

export type PrintReleaseSettingsInput = Partial<PrintReleaseSettings>;

export async function getPrintReleaseSettings(): Promise<PrintReleaseSettings> {
  const response = await authorizedFetch("/api/v1/settings/print-release");
  return response.json();
}

export async function updatePrintReleaseSettings(
  input: PrintReleaseSettingsInput,
): Promise<PrintReleaseSettings> {
  const response = await authorizedFetch("/api/v1/settings/print-release", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export type SnmpDefaults = {
  version: SnmpVersion;
  port: number;
  has_community: boolean;
  enabled: boolean;
  retention_days: number;
};

export type SnmpDefaultsInput = {
  version?: SnmpVersion;
  port?: number;
  community?: string;
  enabled?: boolean;
  retention_days?: number;
};

export async function getSnmpDefaults(): Promise<SnmpDefaults> {
  const response = await authorizedFetch("/api/v1/settings/snmp");
  return response.json();
}

export async function updateSnmpDefaults(input: SnmpDefaultsInput): Promise<SnmpDefaults> {
  const response = await authorizedFetch("/api/v1/settings/snmp", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

// --- Public print-release kiosk API (app/routers/release.py) — no
// PrintOps login involved, so this deliberately does NOT use
// authorizedFetch (no JWT to attach, and its 401->redirect-to-login
// behavior would be wrong for a kiosk visitor who was never logged in). ---

export type HeldJob = {
  id: string;
  status: string;
  document_name: string | null;
  page_count: number | null;
  created_at: string;
  held_expires_at: string | null;
};

export class ReleaseApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function releaseFetch(path: string, body: unknown): Promise<Response> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const responseBody = await response.json().catch(() => ({}));
    throw new ReleaseApiError(
      response.status,
      responseBody.detail ?? `Request failed: ${response.status}`,
    );
  }
  return response;
}

export async function listHeldJobs(token: string, pin: string): Promise<HeldJob[]> {
  const response = await releaseFetch(`/api/v1/release/${encodeURIComponent(token)}/jobs`, { pin });
  return response.json();
}

export async function releaseHeldJob(token: string, jobId: string, pin: string): Promise<HeldJob> {
  const response = await releaseFetch(
    `/api/v1/release/${encodeURIComponent(token)}/jobs/${jobId}/release`,
    { pin },
  );
  return response.json();
}
