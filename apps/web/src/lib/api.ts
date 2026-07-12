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

async function authorizedFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const token = getToken();
  // FormData bodies (multipart file uploads) must NOT get a manual
  // Content-Type — the browser sets its own boundary-bearing value, and
  // overriding it here would corrupt the upload.
  const isFormData = init.body instanceof FormData;
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init.body && !isFormData
        ? { "Content-Type": "application/json" }
        : {}),
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
    throw new ApiError(
      response.status,
      body.detail ?? `Request failed: ${response.status}`,
    );
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
  // The device's currently-selected default (raw PWG name, e.g.
  // "na_letter_8.5x11in") — distinct from media_sizes, which is every size
  // the device merely supports.
  default_media_size: string | null;
  // One entry per tray/input-source the device reports — empty on devices
  // that don't advertise per-tray media (most non-MFP printers).
  media_trays: {
    source: string | null;
    type: string | null;
    width_in: number | null;
    height_in: number | null;
  }[];
  media_sources: string[];
  media_types: string[];
  output_bins: string[];
  finishings: string[];
  collation_supported: boolean;
  pin_printing_supported: boolean;
  accounting_supported: boolean;
  // Advertised (not live-tested) IPPS support — see Printer.use_tls to
  // actually turn TLS on.
  tls_supported: boolean;
};

export type Printer = {
  id: string;
  name: string;
  // True for a virtual Follow-Me queue (no real device behind it) —
  // see createVirtualFollowMeQueue below. ip_address is null for these.
  is_virtual: boolean;
  ip_address: string | null;
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
  archived_at: string | null;
  release_required: boolean;
  follow_me_enabled: boolean;
  release_token: string | null;
  snmp_enabled: boolean;
  snmp_port: number | null;
  snmp_version: SnmpVersion | null;
  has_snmp_community: boolean;
  snmp_vendor_profile: VendorProfile | null;
  ldap_enabled: boolean;
  ldap_bind_username: string | null;
  has_ldap_bind_password: boolean;
  // Reference-only credential storage — see the Printer model's docstring.
  // The plaintext password fields are only ever populated by GET
  // /printers/{id} for an admin requester; null everywhere else
  // (including the list endpoint, and always for a viewer).
  web_login_username: string | null;
  has_web_login_password: boolean;
  web_login_password: string | null;
  scan_email_address: string | null;
  has_scan_password: boolean;
  scan_password: string | null;
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
export type VendorProfile =
  "canon" | "konica_minolta" | "hp" | "lexmark" | "kyocera" | "generic";
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
  follow_me_enabled?: boolean;
  snmp_enabled?: boolean;
  snmp_port?: number | null;
  // "" clears the override back to the global default (see
  // app/routers/printers.py:update_printer) — not just null/omitted.
  snmp_version?: SnmpVersion | "" | null;
  snmp_community?: string | null;
  snmp_vendor_profile?: VendorProfile | "" | null;
  ldap_enabled?: boolean;
  // "" clears the override, same convention as the SNMP fields above.
  ldap_bind_username?: string | null;
  ldap_bind_password?: string | null;
  // "" clears the stored value, same convention as ldap_bind_password.
  web_login_username?: string | null;
  web_login_password?: string | null;
  scan_email_address?: string | null;
  scan_password?: string | null;
};

export type VirtualQueueCreateInput = {
  name: string;
  building?: string | null;
  room?: string | null;
  department?: string | null;
  notes?: string | null;
};

export async function createVirtualFollowMeQueue(
  input: VirtualQueueCreateInput,
): Promise<Printer> {
  const response = await authorizedFetch("/api/v1/printers/virtual", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function listPrinters(params?: { includeArchived?: boolean }): Promise<Printer[]> {
  const qs = params?.includeArchived ? "?include_archived=true" : "";
  const response = await authorizedFetch(`/api/v1/printers${qs}`);
  return response.json();
}

export async function getPrinter(id: string): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}`);
  return response.json();
}

export async function createPrinter(
  input: PrinterCreateInput,
): Promise<Printer> {
  const response = await authorizedFetch("/api/v1/printers", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function updatePrinter(
  id: string,
  input: PrinterUpdateInput,
): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function deletePrinter(id: string): Promise<void> {
  await authorizedFetch(`/api/v1/printers/${id}`, { method: "DELETE" });
}

export async function archivePrinter(id: string): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/archive`, {
    method: "POST",
  });
  return response.json();
}

export async function unarchivePrinter(id: string): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/unarchive`, {
    method: "POST",
  });
  return response.json();
}

export async function rediscoverPrinter(id: string): Promise<Printer> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/discover`, {
    method: "POST",
  });
  return response.json();
}

export async function resyncQueue(id: string): Promise<Printer> {
  const response = await authorizedFetch(
    `/api/v1/printers/${id}/resync-queue`,
    { method: "POST" },
  );
  return response.json();
}

export async function regeneratePrinterReleaseToken(
  id: string,
): Promise<Printer> {
  const response = await authorizedFetch(
    `/api/v1/printers/${id}/regenerate-release-token`,
    {
      method: "POST",
    },
  );
  return response.json();
}

export async function testPrintPrinter(
  id: string,
): Promise<{ message: string }> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/test-print`, {
    method: "POST",
  });
  return response.json();
}

export async function checkPrinterStatus(id: string): Promise<Printer> {
  const response = await authorizedFetch(
    `/api/v1/printers/${id}/check-status`,
    { method: "POST" },
  );
  return response.json();
}

export async function checkPrinterCounters(id: string): Promise<Printer> {
  const response = await authorizedFetch(
    `/api/v1/printers/${id}/check-counters`,
    {
      method: "POST",
    },
  );
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

export type DailyTonerLevel = {
  bucket_start: string;
  black: number | null;
  cyan: number | null;
  magenta: number | null;
  yellow: number | null;
};

export async function getPrinterTonerHistory(
  id: string,
  days: number,
): Promise<DailyTonerLevel[]> {
  const response = await authorizedFetch(
    `/api/v1/printers/${id}/toner-history?days=${days}`,
  );
  return response.json();
}

export async function purgePrinterJobs(
  id: string,
): Promise<{ cancelled_count: number }> {
  const response = await authorizedFetch(`/api/v1/printers/${id}/purge-jobs`, {
    method: "POST",
  });
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
  const response = await authorizedFetch(
    `/api/v1/printers/${id}/mdm-connection`,
  );
  return response.json();
}

export type CupsQueueDefaults = {
  page_size: string | null;
};

export async function getCupsQueueDefaults(id: string): Promise<CupsQueueDefaults> {
  const response = await authorizedFetch(
    `/api/v1/printers/${id}/cups-queue-defaults`,
  );
  return response.json();
}

// --- MFP / copier accounting (Stage 1) ---

export type MfpVendor =
  | "canon"
  | "konica_minolta"
  | "hp"
  | "lexmark"
  | "kyocera"
  | "ricoh"
  | "sharp"
  | "xerox"
  | "generic";

export type DeviceCapabilities = {
  walkup_copy_accounting: boolean | null;
  user_code_pin_auth: boolean | null;
  badge_card_auth: boolean | null;
  department_id_accounting: boolean | null;
  ldap_auth: boolean | null;
  local_user_table: boolean | null;
  remote_user_provisioning: boolean | null;
  csv_accounting_export: boolean | null;
  api_accounting_retrieval: boolean | null;
  snmp_meter_counters: boolean | null;
  scan_accounting: boolean | null;
  color_mono_accounting: boolean | null;
  quotas: boolean | null;
  secure_print_release: boolean | null;
};

export type MfpDevice = {
  id: string;
  printer_id: string | null;
  name: string;
  vendor: MfpVendor;
  model: string | null;
  serial_number: string | null;
  ip_address: string | null;
  hostname: string | null;
  building: string | null;
  room: string | null;
  department: string | null;
  connector_type: string;
  connector_config: Record<string, unknown> | null;
  capabilities: DeviceCapabilities;
  capabilities_source: "manual" | "connector_reported" | null;
  capabilities_detected_at: string | null;
  snmp_enabled: boolean;
  snmp_port: number | null;
  snmp_version: SnmpVersion | null;
  has_snmp_community: boolean;
  snmp_vendor_profile: string | null;
  page_count_total: number | null;
  page_count_copy: number | null;
  page_count_print: number | null;
  page_count_confidence: PageCountConfidence | null;
  page_count_vendor_profile_used: string | null;
  page_count_checked_at: string | null;
  page_count_error: string | null;
  last_test_connection_at: string | null;
  last_test_connection_ok: boolean | null;
  last_test_connection_message: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type ConnectorTypeOption = {
  connector_type: string;
  label: string;
  setup_notes: string | null;
};

export type MfpDeviceCreateInput = {
  name: string;
  vendor?: MfpVendor;
  model?: string | null;
  serial_number?: string | null;
  printer_id?: string | null;
  ip_address?: string | null;
  hostname?: string | null;
  building?: string | null;
  room?: string | null;
  department?: string | null;
  connector_type?: string;
  connector_config?: Record<string, unknown> | null;
  snmp_enabled?: boolean;
  snmp_port?: number | null;
  snmp_version?: SnmpVersion | null;
  snmp_community?: string | null;
  snmp_vendor_profile?: string | null;
  notes?: string | null;
};

export type MfpDeviceUpdateInput = Partial<MfpDeviceCreateInput> & {
  capabilities?: Partial<DeviceCapabilities>;
};

export async function listConnectorTypes(): Promise<ConnectorTypeOption[]> {
  const response = await authorizedFetch("/api/v1/mfp-devices/connector-types");
  return response.json();
}

export async function listMfpDevices(): Promise<MfpDevice[]> {
  const response = await authorizedFetch("/api/v1/mfp-devices");
  return response.json();
}

export async function getMfpDevice(id: string): Promise<MfpDevice> {
  const response = await authorizedFetch(`/api/v1/mfp-devices/${id}`);
  return response.json();
}

export async function createMfpDevice(
  input: MfpDeviceCreateInput,
): Promise<MfpDevice> {
  const response = await authorizedFetch("/api/v1/mfp-devices", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function updateMfpDevice(
  id: string,
  input: MfpDeviceUpdateInput,
): Promise<MfpDevice> {
  const response = await authorizedFetch(`/api/v1/mfp-devices/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function deleteMfpDevice(id: string): Promise<void> {
  await authorizedFetch(`/api/v1/mfp-devices/${id}`, { method: "DELETE" });
}

export async function testMfpDeviceConnection(id: string): Promise<MfpDevice> {
  const response = await authorizedFetch(
    `/api/v1/mfp-devices/${id}/test-connection`,
    {
      method: "POST",
    },
  );
  return response.json();
}

export async function checkMfpDeviceCapabilities(
  id: string,
): Promise<MfpDevice> {
  const response = await authorizedFetch(
    `/api/v1/mfp-devices/${id}/check-capabilities`,
    {
      method: "POST",
    },
  );
  return response.json();
}

export async function checkMfpDeviceMeter(id: string): Promise<MfpDevice> {
  const response = await authorizedFetch(
    `/api/v1/mfp-devices/${id}/check-meter`,
    {
      method: "POST",
    },
  );
  return response.json();
}

export type CopierUsageRecord = {
  id: string;
  mfp_device_id: string;
  vendor: string;
  model: string | null;
  serial_number: string | null;
  location_building: string | null;
  staff_email: string | null;
  staff_employee_id: string | null;
  external_identity_used: string;
  external_identity_type: string | null;
  authentication_method: string | null;
  activity_type: string;
  page_count: number | null;
  sheet_count: number | null;
  color_page_count: number | null;
  monochrome_page_count: number | null;
  duplex: boolean | null;
  paper_size: string | null;
  occurred_at: string | null;
  period_start: string | null;
  period_end: string | null;
  source_connector: string;
  import_batch_id: string | null;
  created_at: string;
  updated_at: string;
};

export async function listMfpDeviceUsage(
  id: string,
  limit = 100,
): Promise<CopierUsageRecord[]> {
  const response = await authorizedFetch(
    `/api/v1/mfp-devices/${id}/usage?limit=${limit}`,
  );
  return response.json();
}

export type CopierIdentityType =
  | "staff_id"
  | "pin"
  | "badge_id"
  | "department_id"
  | "user_code"
  | "vendor_user_id"
  | "email";

export type StaffCopierIdentity = {
  id: string;
  staff_email: string;
  identity_type: CopierIdentityType;
  identity_value: string;
  mfp_device_id: string | null;
  note: string | null;
  created_at: string;
  updated_at: string;
};

export type StaffCopierIdentityCreateInput = {
  staff_email: string;
  identity_type: CopierIdentityType;
  identity_value: string;
  mfp_device_id?: string | null;
  note?: string | null;
};

export type StaffCopierIdentityUpdateInput = Partial<
  Omit<StaffCopierIdentityCreateInput, "staff_email">
>;

export async function listStaffCopierIdentities(params?: {
  staff_email?: string;
  identity_type?: string;
}): Promise<StaffCopierIdentity[]> {
  const query = new URLSearchParams();
  if (params?.staff_email) query.set("staff_email", params.staff_email);
  if (params?.identity_type) query.set("identity_type", params.identity_type);
  const qs = query.toString();
  const response = await authorizedFetch(
    `/api/v1/staff-copier-identities${qs ? `?${qs}` : ""}`,
  );
  return response.json();
}

export async function listStaffMissingCopierIdentity(): Promise<
  GoogleWorkspaceUserEntry[]
> {
  const response = await authorizedFetch(
    "/api/v1/staff-copier-identities/missing",
  );
  return response.json();
}

export async function getStaffCopierIdentitiesByEmail(
  email: string,
): Promise<StaffCopierIdentity[]> {
  const response = await authorizedFetch(
    `/api/v1/staff-copier-identities/by-staff/${encodeURIComponent(email)}`,
  );
  return response.json();
}

export async function createStaffCopierIdentity(
  input: StaffCopierIdentityCreateInput,
): Promise<StaffCopierIdentity> {
  const response = await authorizedFetch("/api/v1/staff-copier-identities", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function updateStaffCopierIdentity(
  id: string,
  input: StaffCopierIdentityUpdateInput,
): Promise<StaffCopierIdentity> {
  const response = await authorizedFetch(
    `/api/v1/staff-copier-identities/${id}`,
    {
      method: "PATCH",
      body: JSON.stringify(input),
    },
  );
  return response.json();
}

export async function deleteStaffCopierIdentity(id: string): Promise<void> {
  await authorizedFetch(`/api/v1/staff-copier-identities/${id}`, {
    method: "DELETE",
  });
}

// --- Copier accounting imports (Stage 1) ---

export type CopierImportTemplate = {
  id: string;
  name: string;
  vendor: string;
  model: string | null;
  column_mapping: Record<string, string>;
  identity_type: CopierIdentityType;
  delimiter: string;
  created_by: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type CopierImportTemplateInput = {
  name: string;
  vendor: string;
  model?: string | null;
  column_mapping: Record<string, string>;
  identity_type: CopierIdentityType;
  delimiter?: string;
  notes?: string | null;
};

export async function listCopierImportTemplates(): Promise<
  CopierImportTemplate[]
> {
  const response = await authorizedFetch("/api/v1/copier-imports/templates");
  return response.json();
}

export async function createCopierImportTemplate(
  input: CopierImportTemplateInput,
): Promise<CopierImportTemplate> {
  const response = await authorizedFetch("/api/v1/copier-imports/templates", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function updateCopierImportTemplate(
  id: string,
  input: Partial<CopierImportTemplateInput>,
): Promise<CopierImportTemplate> {
  const response = await authorizedFetch(
    `/api/v1/copier-imports/templates/${id}`,
    {
      method: "PATCH",
      body: JSON.stringify(input),
    },
  );
  return response.json();
}

export async function deleteCopierImportTemplate(id: string): Promise<void> {
  await authorizedFetch(`/api/v1/copier-imports/templates/${id}`, {
    method: "DELETE",
  });
}

export type CopierImportUpload = {
  batch_id: string;
  original_filename: string;
  header: string[];
  sample_rows: Record<string, string>[];
  suggested_mapping: Record<string, string> | null;
  suggested_identity_type: string | null;
  row_count: number;
};

export async function uploadCopierImportFile(
  deviceId: string,
  file: File,
  templateId?: string,
): Promise<CopierImportUpload> {
  const formData = new FormData();
  formData.append("device_id", deviceId);
  if (templateId) formData.append("template_id", templateId);
  formData.append("file", file);
  const response = await authorizedFetch("/api/v1/copier-imports/upload", {
    method: "POST",
    body: formData,
  });
  return response.json();
}

export type CopierImportPreviewRow = {
  row_number: number;
  external_identity_used: string | null;
  staff_email: string | null;
  is_duplicate: boolean;
  error: string | null;
};

export type CopierImportPreview = {
  batch_id: string;
  total_rows: number;
  valid_rows: number;
  duplicate_rows: number;
  unmapped_rows: number;
  error_rows: number;
  sample_rows: CopierImportPreviewRow[];
  saved_template_id: string | null;
};

export async function previewCopierImportBatch(
  batchId: string,
  input: {
    column_mapping: Record<string, string>;
    identity_type: CopierIdentityType;
    period_label?: string | null;
    save_as_template?: CopierImportTemplateInput | null;
  },
): Promise<CopierImportPreview> {
  const response = await authorizedFetch(
    `/api/v1/copier-imports/${batchId}/preview`,
    {
      method: "POST",
      body: JSON.stringify(input),
    },
  );
  return response.json();
}

export type CopierImportBatch = {
  id: string;
  mfp_device_id: string;
  template_id: string | null;
  original_filename: string;
  uploaded_by: string;
  period_label: string | null;
  status: "uploaded" | "previewed" | "committed" | "failed";
  column_mapping: Record<string, string> | null;
  identity_type: string | null;
  row_count: number;
  imported_row_count: number;
  duplicate_row_count: number;
  unmapped_identity_count: number;
  error_detail: { row_number: number; message: string }[] | null;
  committed_at: string | null;
  created_at: string;
  updated_at: string;
};

export async function commitCopierImportBatch(
  batchId: string,
  skipDuplicates = true,
): Promise<CopierImportBatch> {
  const response = await authorizedFetch(
    `/api/v1/copier-imports/${batchId}/commit`,
    {
      method: "POST",
      body: JSON.stringify({ skip_duplicates: skipDuplicates }),
    },
  );
  return response.json();
}

export async function listCopierImportBatches(
  deviceId?: string,
): Promise<CopierImportBatch[]> {
  const qs = deviceId ? `?mfp_device_id=${deviceId}` : "";
  const response = await authorizedFetch(`/api/v1/copier-imports/batches${qs}`);
  return response.json();
}

export async function getCopierImportBatch(
  id: string,
): Promise<CopierImportBatch> {
  const response = await authorizedFetch(
    `/api/v1/copier-imports/batches/${id}`,
  );
  return response.json();
}

export async function deleteCopierImportBatch(id: string): Promise<void> {
  await authorizedFetch(`/api/v1/copier-imports/batches/${id}`, {
    method: "DELETE",
  });
}

export type UnmappedCopierIdentityGroup = {
  mfp_device_id: string;
  external_identity_used: string;
  occurrence_count: number;
  first_seen: string;
  last_seen: string;
  attempted_identity_type: CopierIdentityType | null;
  sample_raw_payload: Record<string, string>;
};

export type ResolveUnmappedInput = {
  mfp_device_id?: string | null;
  identity_type: CopierIdentityType;
  identity_value: string;
  resolved_email: string;
  note?: string | null;
};

export type ResolveUnmappedResult = {
  resolved_email: string;
  identity_type: CopierIdentityType;
  identity_value: string;
  mfp_device_id: string | null;
  backfilled_row_count: number;
};

export async function listUnmappedCopierActivity(): Promise<
  UnmappedCopierIdentityGroup[]
> {
  const response = await authorizedFetch("/api/v1/copier-unmapped");
  return response.json();
}

export async function resolveUnmappedCopierActivity(
  input: ResolveUnmappedInput,
): Promise<ResolveUnmappedResult> {
  const response = await authorizedFetch("/api/v1/copier-unmapped/resolve", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export type JobStatus =
  "received" | "forwarding" | "forwarded" | "failed" | "cancelled" | "held";
export type AttributionMethod =
  "cups" | "mosyle" | "google_workspace" | "unresolved";
export type HoldReason = "pin_release" | "quota" | null;

export type Job = {
  id: string;
  printer_id: string;
  printer_name: string;
  // Resolved from mac_address (Mosyle/Google Workspace device name), the
  // raw MAC if unresolved, or null if this job never got a MAC at all.
  device_name: string | null;
  cups_job_id: number | null;
  submitted_by: string | null;
  // Resolved from submitted_by — the synced Google Workspace name if
  // known, else the email's local-part as a readable stand-in, or null
  // if this job has no submitted_by at all.
  submitted_by_name: string | null;
  attribution_method: AttributionMethod;
  file_size_bytes: number | null;
  page_count: number | null;
  status: JobStatus;
  hold_reason: HoldReason;
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

export async function listJobs(params?: {
  printer_id?: string;
  submitted_by?: string;
  limit?: number;
}): Promise<Job[]> {
  const query = new URLSearchParams();
  if (params?.printer_id) query.set("printer_id", params.printer_id);
  if (params?.submitted_by) query.set("submitted_by", params.submitted_by);
  if (params?.limit) query.set("limit", String(params.limit));
  const qs = query.toString();
  const response = await authorizedFetch(`/api/v1/jobs${qs ? `?${qs}` : ""}`);
  return response.json();
}

export async function listQuotaHolds(): Promise<Job[]> {
  const response = await authorizedFetch("/api/v1/quota-holds");
  return response.json();
}

export async function releaseQuotaHold(jobId: string): Promise<Job> {
  const response = await authorizedFetch(
    `/api/v1/quota-holds/${jobId}/release`,
    {
      method: "POST",
    },
  );
  return response.json();
}

export type UserUsage = {
  email: string | null;
  name: string | null;
  is_other: boolean;
  job_count: number;
  total_pages: number;
  total_bytes: number;
  duplex_pages: number;
  simplex_pages: number;
  mono_pages: number;
  color_pages: number;
  estimated_cost: number;
};

export type UserUsagePage = {
  items: UserUsage[];
  total: number;
  page: number;
  page_size: number;
};

export async function getJobUsage(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
}): Promise<UserUsagePage> {
  const query = new URLSearchParams();
  if (params?.page) query.set("page", String(params.page));
  if (params?.pageSize) query.set("page_size", String(params.pageSize));
  if (params?.search) query.set("search", params.search);
  const qs = query.toString();
  const response = await authorizedFetch(`/api/v1/jobs/usage${qs ? `?${qs}` : ""}`);
  return response.json();
}

export async function getUserUsage(email: string): Promise<UserUsage> {
  const response = await authorizedFetch(`/api/v1/jobs/usage/${encodeURIComponent(email)}`);
  return response.json();
}

export async function cancelJob(id: string): Promise<Job> {
  const response = await authorizedFetch(`/api/v1/jobs/${id}/cancel`, {
    method: "POST",
  });
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

export async function updateMosyleSettings(
  input: MosyleSettingsInput,
): Promise<MosyleSettings> {
  const response = await authorizedFetch("/api/v1/settings/mosyle", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function testMosyleConnection(
  input: MosyleSettingsInput,
): Promise<MosyleTestResult> {
  const response = await authorizedFetch("/api/v1/settings/mosyle/test", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function syncMosyleDevices(): Promise<MosyleSettings> {
  const response = await authorizedFetch("/api/v1/settings/mosyle/sync", {
    method: "POST",
  });
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
  auto_create_copier_identity_from_employee_id: boolean;
  auto_copier_identity_type: CopierIdentityType;
};

export type GoogleWorkspaceSettingsInput = {
  service_account_json?: string;
  admin_email?: string;
  customer_id?: string;
  enabled?: boolean;
  staff_org_unit_path?: string;
  auto_create_copier_identity_from_employee_id?: boolean;
  auto_copier_identity_type?: CopierIdentityType;
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
  const response = await authorizedFetch(
    "/api/v1/settings/google-workspace/test",
    {
      method: "POST",
      body: JSON.stringify(input),
    },
  );
  return response.json();
}

export async function syncGoogleWorkspaceDevices(): Promise<GoogleWorkspaceSettings> {
  const response = await authorizedFetch(
    "/api/v1/settings/google-workspace/sync",
    { method: "POST" },
  );
  return response.json();
}

export type Role = "admin" | "viewer" | "ou_viewer";

export type CurrentUser = {
  username: string;
  role: Role;
  email: string | null;
  name: string | null;
  // Display-only for "ou_viewer" accounts — see app.deps.get_current_user's
  // docstring; enforcement always re-reads the User row server-side.
  granted_ou_paths: string[] | null;
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
  exempt_from_timeout: boolean;
  granted_ou_paths: string[] | null;
};

export type UserAccountPage = {
  items: UserAccount[];
  total: number;
  page: number;
  page_size: number;
};

export async function listUsers(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
  role?: Role;
}): Promise<UserAccountPage> {
  const query = new URLSearchParams();
  if (params?.page) query.set("page", String(params.page));
  if (params?.pageSize) query.set("page_size", String(params.pageSize));
  if (params?.search) query.set("search", params.search);
  if (params?.role) query.set("role", params.role);
  const qs = query.toString();
  const response = await authorizedFetch(`/api/v1/users${qs ? `?${qs}` : ""}`);
  return response.json();
}

export async function updateUser(
  id: string,
  input: {
    role?: Role;
    is_active?: boolean;
    exempt_from_timeout?: boolean;
    granted_ou_paths?: string[] | null;
  },
): Promise<UserAccount> {
  const response = await authorizedFetch(`/api/v1/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
  return response.json();
}

// Pre-provisions an account by email before their first Google sign-in —
// google_sub stays null until then; /auth/google/callback matches this row
// by email on that first login instead of creating a duplicate (see that
// endpoint's docstring on the backend).
export async function createUser(input: {
  email: string;
  role: Role;
  granted_ou_paths?: string[] | null;
}): Promise<UserAccount> {
  const response = await authorizedFetch("/api/v1/users", {
    method: "POST",
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

export type KnownDevicePage = {
  items: KnownDevice[];
  total: number;
  page: number;
  page_size: number;
};

export async function listKnownDevices(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
}): Promise<KnownDevicePage> {
  const query = new URLSearchParams();
  if (params?.page) query.set("page", String(params.page));
  if (params?.pageSize) query.set("page_size", String(params.pageSize));
  if (params?.search) query.set("search", params.search);
  const qs = query.toString();
  const response = await authorizedFetch(`/api/v1/devices${qs ? `?${qs}` : ""}`);
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
  const response = await authorizedFetch(
    `/api/v1/devices/${encodeURIComponent(macAddress)}/override`,
    {
      method: "PUT",
      body: JSON.stringify(input),
    },
  );
  return response.json();
}

export async function deleteDeviceOverride(macAddress: string): Promise<void> {
  await authorizedFetch(
    `/api/v1/devices/${encodeURIComponent(macAddress)}/override`,
    {
      method: "DELETE",
    },
  );
}

export type AttributionAlias = {
  id: string;
  alias: string;
  resolved_email: string;
  source: "manual" | "google_workspace_sync";
  note: string | null;
  created_at: string;
  updated_at: string;
  backfilled_job_count: number;
};

export type AttributionAliasPage = {
  items: AttributionAlias[];
  total: number;
  page: number;
  page_size: number;
};

export async function listAttributionAliases(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
}): Promise<AttributionAliasPage> {
  const query = new URLSearchParams();
  if (params?.page) query.set("page", String(params.page));
  if (params?.pageSize) query.set("page_size", String(params.pageSize));
  if (params?.search) query.set("search", params.search);
  const qs = query.toString();
  const response = await authorizedFetch(
    `/api/v1/attribution-aliases${qs ? `?${qs}` : ""}`,
  );
  return response.json();
}

export async function createAttributionAlias(input: {
  alias: string;
  resolved_email: string;
  note?: string | null;
}): Promise<AttributionAlias> {
  const response = await authorizedFetch("/api/v1/attribution-aliases", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function deleteAttributionAlias(id: string): Promise<void> {
  await authorizedFetch(`/api/v1/attribution-aliases/${id}`, {
    method: "DELETE",
  });
}

export type GoogleWorkspaceUserEntry = {
  email: string;
  name: string | null;
  employee_id: string | null;
  aliases: string[] | null;
};

export async function listGoogleWorkspaceUsers(): Promise<
  GoogleWorkspaceUserEntry[]
> {
  const response = await authorizedFetch(
    "/api/v1/settings/google-workspace/users",
  );
  return response.json();
}

// Distinct org_unit_path values from the synced roster — powers the OU
// picker on Settings > Permissions so an admin picks from real, currently-
// populated org units instead of typing a path blind.
export async function listGoogleWorkspaceOrgUnits(): Promise<string[]> {
  const response = await authorizedFetch(
    "/api/v1/settings/google-workspace/org-units",
  );
  return response.json();
}

/** Downloads a CSV of Name/Email/PIN (PIN = Google Workspace Employee ID)
 * for loading into a copier's local PIN list — see
 * app/routers/settings.py:export_copier_pin_roster for the caveat that the
 * column layout is a starting point, not a confirmed match for any
 * specific device's bulk-import format. */
export async function downloadCopierPinRoster(): Promise<void> {
  const response = await authorizedFetch(
    "/api/v1/settings/google-workspace/copier-pin-roster.csv",
  );
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

export type ZabbixSettings = {
  enabled: boolean;
  // Always returned in full, never masked — a rotatable capability token
  // meant to be copied into a Zabbix host macro, not a login secret (same
  // convention as Printer.release_token).
  api_token: string | null;
  base_url: string | null;
};

export type ZabbixSettingsInput = {
  enabled?: boolean;
  base_url?: string;
};

export async function getZabbixSettings(): Promise<ZabbixSettings> {
  const response = await authorizedFetch("/api/v1/settings/zabbix");
  return response.json();
}

export async function updateZabbixSettings(
  input: ZabbixSettingsInput,
): Promise<ZabbixSettings> {
  const response = await authorizedFetch("/api/v1/settings/zabbix", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export async function regenerateZabbixToken(): Promise<ZabbixSettings> {
  const response = await authorizedFetch("/api/v1/settings/zabbix/regenerate-token", {
    method: "POST",
  });
  return response.json();
}

/** Downloads the generic Zabbix template — routed through authorizedFetch
 * + blob (not a plain <a href>), same as downloadCopierPinRoster, since
 * this endpoint is admin-JWT-gated and a bare navigation link can't
 * attach the Authorization header. */
export async function downloadZabbixTemplate(): Promise<void> {
  const response = await authorizedFetch("/api/v1/settings/zabbix/template");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "printops_zabbix_template.yaml";
  a.click();
  URL.revokeObjectURL(url);
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

function buildReportQuery(
  filters?: ReportFilters,
  extra?: Record<string, string>,
): string {
  const query = new URLSearchParams();
  if (filters?.start) query.set("start", filters.start);
  if (filters?.end) query.set("end", filters.end);
  if (filters?.building) query.set("building", filters.building);
  if (filters?.department) query.set("department", filters.department);
  if (filters?.printer_id) query.set("printer_id", filters.printer_id);
  if (filters?.submitted_by) query.set("submitted_by", filters.submitted_by);
  if (filters?.status) query.set("status", filters.status);
  if (filters?.color_mode) query.set("color_mode", filters.color_mode);
  if (filters?.duplex !== undefined)
    query.set("duplex", String(filters.duplex));
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

export async function getReportSummary(
  filters?: ReportFilters,
): Promise<ReportSummary> {
  const qs = buildReportQuery(filters);
  const response = await authorizedFetch(
    `/api/v1/reports/summary${qs ? `?${qs}` : ""}`,
  );
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
  const response = await authorizedFetch(
    `/api/v1/reports/timeline${qs ? `?${qs}` : ""}`,
  );
  return response.json();
}

export type HourlyBucket = {
  interval: number;
  total_pages: number;
  color_pages: number;
  mono_pages: number;
  duplex_pages: number;
  simplex_pages: number;
  job_count: number;
  // Tracked walk-up copies only (CopierUsageRecord) — see HourlyBucketOut
  // in app/schemas/report.py for why untracked/estimated copies aren't here.
  copy_pages: number;
  copy_count: number;
};

// start/end are full ISO instants (typically the viewer's local midnight
// through now) — see app/reports/aggregation.py:get_hourly_timeline's
// docstring for why this endpoint has no server-side notion of "today".
export async function getLiveHourly(start: Date, end: Date): Promise<HourlyBucket[]> {
  const query = new URLSearchParams({
    start: start.toISOString(),
    end: end.toISOString(),
  });
  const response = await authorizedFetch(`/api/v1/reports/live/hourly?${query.toString()}`);
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
  const response = await authorizedFetch(
    `/api/v1/reports/leaderboard${qs ? `?${qs}` : ""}`,
  );
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
  groupBy: "printer" | "user" | "device",
  filters?: ReportFilters,
): Promise<CostEntry[]> {
  const qs = buildReportQuery(filters, { group_by: groupBy });
  const response = await authorizedFetch(
    `/api/v1/reports/cost-breakdown${qs ? `?${qs}` : ""}`,
  );
  return response.json();
}

export type UntrackedCopyPrinterEntry = {
  printer_id: string;
  printer_name: string;
  measured_copies: number;
  estimated_untracked: number;
};

export type UntrackedCopySummary = {
  measured_copies: number;
  estimated_untracked: number;
  tracking_since: string | null;
  printers: UntrackedCopyPrinterEntry[];
};

export async function getUntrackedCopySummary(
  filters?: ReportFilters,
): Promise<UntrackedCopySummary> {
  const qs = buildReportQuery(filters);
  const response = await authorizedFetch(
    `/api/v1/reports/untracked-copies${qs ? `?${qs}` : ""}`,
  );
  return response.json();
}

export type PeakTimes = {
  by_day_of_week: Record<string, number>;
  by_hour: Record<string, number>;
};

export async function getReportPeakTimes(
  filters?: ReportFilters,
): Promise<PeakTimes> {
  const qs = buildReportQuery(filters);
  const response = await authorizedFetch(
    `/api/v1/reports/peak-times${qs ? `?${qs}` : ""}`,
  );
  return response.json();
}

export async function getReportFunFacts(
  periodLabel: string,
  filters?: ReportFilters,
): Promise<string[]> {
  const qs = buildReportQuery(filters, { period_label: periodLabel });
  const response = await authorizedFetch(
    `/api/v1/reports/fun-facts${qs ? `?${qs}` : ""}`,
  );
  const body: { facts: string[] } = await response.json();
  return body.facts;
}

/** Downloads the filtered job export as a CSV file — routed through
 * authorizedFetch (unlike a plain <a href>) so the JWT bearer token
 * actually reaches the API; browsers don't attach custom headers to a
 * navigation click. */
export async function downloadReportCsv(
  filters?: ReportFilters,
): Promise<void> {
  const qs = buildReportQuery(filters);
  const response = await authorizedFetch(
    `/api/v1/reports/export.csv${qs ? `?${qs}` : ""}`,
  );
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "print-insights-export.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export type CombinedSummary = {
  print_pages: number;
  copy_pages: number;
  total_pages: number;
  unmapped_copy_activity_count: number;
};

export async function getCombinedReportSummary(
  filters?: ReportFilters,
): Promise<CombinedSummary> {
  const qs = buildReportQuery(filters);
  const response = await authorizedFetch(
    `/api/v1/reports/combined-summary${qs ? `?${qs}` : ""}`,
  );
  return response.json();
}

export type CombinedLeaderboardEntry = {
  key: string;
  label: string;
  print_pages: number;
  copy_pages: number;
  total_pages: number;
  color_pages: number;
  mono_pages: number;
  duplex_pages: number;
  simplex_pages: number;
  // Print-only -- walk-up copy usage has no cost model.
  estimated_cost: number;
};

export async function getCombinedUserLeaderboard(
  filters?: ReportFilters,
  limit = 10,
): Promise<CombinedLeaderboardEntry[]> {
  const qs = buildReportQuery(filters, { limit: String(limit) });
  const response = await authorizedFetch(
    `/api/v1/reports/combined-leaderboard${qs ? `?${qs}` : ""}`,
  );
  return response.json();
}

export async function downloadCombinedReportCsv(
  filters?: ReportFilters,
): Promise<void> {
  const qs = buildReportQuery(filters);
  const response = await authorizedFetch(
    `/api/v1/reports/export-combined.csv${qs ? `?${qs}` : ""}`,
  );
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "combined-print-copy-export.csv";
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
  await authorizedFetch(`/api/v1/reports/snapshots/${id}`, {
    method: "DELETE",
  });
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

export type UntrackedCopySettings = {
  enabled: boolean;
  enabled_at: string | null;
};

export async function getUntrackedCopySettings(): Promise<UntrackedCopySettings> {
  const response = await authorizedFetch("/api/v1/settings/untracked-copies");
  return response.json();
}

export async function updateUntrackedCopySettings(
  enabled: boolean,
): Promise<UntrackedCopySettings> {
  const response = await authorizedFetch("/api/v1/settings/untracked-copies", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
  return response.json();
}

export type CartridgeColor = "black" | "cyan" | "magenta" | "yellow";

export type Cartridge = {
  color: CartridgeColor;
  cost: number;
  yield_pages: number;
  // Reference-only part number for this color slot, e.g. "TN-227C" — see
  // PrinterTonerCartridge.model's docstring (app/models/report.py).
  model: string | null;
  // See PrinterTonerCartridge.warning_threshold_percent's docstring.
  warning_threshold_percent: number;
  // SNMP-detected, read-only — see PrinterTonerCartridge.detected_*'s
  // docstring (app/models/report.py). null until the first successful
  // detectPrinterCartridges call.
  detected_description: string | null;
  detected_high_capacity: boolean | null;
  detected_at: string | null;
  // Live-polled, read-only — see PrinterTonerCartridge.current_level_percent's
  // docstring. null until the first successful detect/background poll.
  current_level_percent: number | null;
  level_checked_at: string | null;
};

export type CartridgeInput = {
  color: CartridgeColor;
  cost: number;
  yield_pages: number;
  model: string | null;
  warning_threshold_percent: number;
};

export async function getPrinterCartridges(
  printerId: string,
): Promise<Cartridge[]> {
  const response = await authorizedFetch(
    `/api/v1/printers/${printerId}/toner-cartridges`,
  );
  return response.json();
}

export async function updatePrinterCartridges(
  printerId: string,
  cartridges: CartridgeInput[],
): Promise<Cartridge[]> {
  const response = await authorizedFetch(
    `/api/v1/printers/${printerId}/toner-cartridges`,
    {
      method: "PUT",
      body: JSON.stringify(cartridges),
    },
  );
  return response.json();
}

export type DetectedSupply = {
  description: string;
  color: CartridgeColor | null;
  high_capacity: boolean | null;
  level_percent: number | null;
};

export type DetectCartridgesResult = {
  cartridges: Cartridge[];
  unmatched: DetectedSupply[];
};

export async function detectPrinterCartridges(
  printerId: string,
): Promise<DetectCartridgesResult> {
  const response = await authorizedFetch(
    `/api/v1/printers/${printerId}/toner-cartridges/detect`,
    { method: "POST" },
  );
  return response.json();
}

export type FleetCartridge = {
  id: string;
  printer_id: string;
  printer_name: string;
  printer_manufacturer: string | null;
  printer_model: string | null;
  building: string | null;
  room: string | null;
  color: CartridgeColor;
  cost: number;
  yield_pages: number;
  model: string | null;
  warning_threshold_percent: number;
  current_level_percent: number | null;
};

export async function listFleetTonerCartridges(): Promise<FleetCartridge[]> {
  const response = await authorizedFetch("/api/v1/printers/toner-cartridges");
  return response.json();
}

export type BulkCartridgeUpdate = {
  id: string;
  cost: number;
  yield_pages: number;
  model: string | null;
};

export async function bulkUpdateTonerCartridges(
  updates: BulkCartridgeUpdate[],
): Promise<FleetCartridge[]> {
  const response = await authorizedFetch("/api/v1/printers/toner-cartridges/bulk", {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
  return response.json();
}

export type QuotaPeriod =
  "daily" | "weekly" | "monthly" | "quarterly" | "yearly";

export type PrinterQuota = {
  id: string;
  printer_id: string;
  user_email: string | null;
  period: QuotaPeriod;
  page_limit: number;
  pages_used: number;
};

export type PrinterQuotaInput = {
  user_email?: string | null;
  period: QuotaPeriod;
  page_limit: number;
};

export type PrinterQuotaUpdateInput = {
  period?: QuotaPeriod;
  page_limit?: number;
};

export async function listPrinterQuotas(
  printerId: string,
): Promise<PrinterQuota[]> {
  const response = await authorizedFetch(
    `/api/v1/printers/${printerId}/quotas`,
  );
  return response.json();
}

export async function createPrinterQuota(
  printerId: string,
  input: PrinterQuotaInput,
): Promise<PrinterQuota> {
  const response = await authorizedFetch(
    `/api/v1/printers/${printerId}/quotas`,
    {
      method: "POST",
      body: JSON.stringify(input),
    },
  );
  return response.json();
}

export async function updatePrinterQuota(
  printerId: string,
  quotaId: string,
  input: PrinterQuotaUpdateInput,
): Promise<PrinterQuota> {
  const response = await authorizedFetch(
    `/api/v1/printers/${printerId}/quotas/${quotaId}`,
    {
      method: "PATCH",
      body: JSON.stringify(input),
    },
  );
  return response.json();
}

export async function deletePrinterQuota(
  printerId: string,
  quotaId: string,
): Promise<void> {
  await authorizedFetch(`/api/v1/printers/${printerId}/quotas/${quotaId}`, {
    method: "DELETE",
  });
}

export type PrinterReleaseBypass = {
  id: string;
  printer_id: string;
  user_email: string;
};

export async function listPrinterReleaseBypasses(
  printerId: string,
): Promise<PrinterReleaseBypass[]> {
  const response = await authorizedFetch(
    `/api/v1/printers/${printerId}/release-bypasses`,
  );
  return response.json();
}

export async function createPrinterReleaseBypass(
  printerId: string,
  userEmail: string,
): Promise<PrinterReleaseBypass> {
  const response = await authorizedFetch(
    `/api/v1/printers/${printerId}/release-bypasses`,
    {
      method: "POST",
      body: JSON.stringify({ user_email: userEmail }),
    },
  );
  return response.json();
}

export async function deletePrinterReleaseBypass(
  printerId: string,
  bypassId: string,
): Promise<void> {
  await authorizedFetch(
    `/api/v1/printers/${printerId}/release-bypasses/${bypassId}`,
    { method: "DELETE" },
  );
}

export type QuotaSettings = {
  enabled: boolean;
};

export async function getQuotaSettings(): Promise<QuotaSettings> {
  const response = await authorizedFetch("/api/v1/settings/quotas");
  return response.json();
}

export async function updateQuotaSettings(
  input: Partial<QuotaSettings>,
): Promise<QuotaSettings> {
  const response = await authorizedFetch("/api/v1/settings/quotas", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export type SessionSettings = {
  idle_timeout_minutes: number;
};

export async function getSessionSettings(): Promise<SessionSettings> {
  const response = await authorizedFetch("/api/v1/settings/session");
  return response.json();
}

export async function updateSessionSettings(
  input: Partial<SessionSettings>,
): Promise<SessionSettings> {
  const response = await authorizedFetch("/api/v1/settings/session", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

// Reissues the current token with a renewed expiry — see
// apps/web/src/lib/idleRefresh.ts, which calls this periodically only
// while there's been real activity, implementing the idle-timeout on the
// frontend side (app/routers/auth.py's /auth/refresh docstring has the
// other half). Deliberately NOT wrapped in authorizedFetch's usual 401 ->
// redirect-to-login handling for this one call: a 401 here just means the
// idle window already lapsed, which is the expected end state, not an
// error to bounce on specially — the very next authorizedFetch call will
// naturally hit that same 401 and redirect anyway.
export async function refreshSession(): Promise<{ access_token: string }> {
  const token = getToken();
  const response = await fetch(`${API_URL}/auth/refresh`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    throw new ApiError(response.status, "Session refresh failed");
  }
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

export async function updateSnmpDefaults(
  input: SnmpDefaultsInput,
): Promise<SnmpDefaults> {
  const response = await authorizedFetch("/api/v1/settings/snmp", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export type TlsCertificateStatus = {
  issuer: string;
  expires_at: string;
  days_remaining: number;
};

export type ServerSettings = {
  hostname: string;
  require_encryption: boolean;
  advertise_ipps: boolean;
  sync_error: string | null;
  // null until scripts/sync_server_settings.sh has synced a certificate at
  // least once (or on a box with no Caddy-issued cert for this hostname).
  certificate: TlsCertificateStatus | null;
};

export type ServerSettingsInput = {
  hostname?: string;
  require_encryption?: boolean;
  advertise_ipps?: boolean;
};

export async function getServerSettings(): Promise<ServerSettings> {
  const response = await authorizedFetch("/api/v1/settings/server");
  return response.json();
}

export async function updateServerSettings(
  input: ServerSettingsInput,
): Promise<ServerSettings> {
  const response = await authorizedFetch("/api/v1/settings/server", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

// Re-runs the cupsd.conf/cert/Avahi sync without changing any field — for
// retrying after a transient failure, or picking up a freshly-issued/
// renewed Caddy certificate immediately rather than waiting for the daily
// background sync or the next unrelated settings save.
export async function syncServerSettingsNow(): Promise<ServerSettings> {
  const response = await authorizedFetch("/api/v1/settings/server/sync", {
    method: "POST",
  });
  return response.json();
}

export type LdapRelaySettings = {
  enabled: boolean;
  base_dn: string;
  port: number;
};

export type LdapRelaySettingsInput = Partial<LdapRelaySettings>;

export async function getLdapRelaySettings(): Promise<LdapRelaySettings> {
  const response = await authorizedFetch("/api/v1/settings/ldap");
  return response.json();
}

export async function updateLdapRelaySettings(
  input: LdapRelaySettingsInput,
): Promise<LdapRelaySettings> {
  const response = await authorizedFetch("/api/v1/settings/ldap", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export type SyslogSeverity =
  | "emerg"
  | "alert"
  | "crit"
  | "err"
  | "warning"
  | "notice"
  | "info"
  | "debug";

export type SyslogSettings = {
  enabled: boolean;
  port: number;
  min_severity: SyslogSeverity;
  retention_days: number;
};

export type SyslogSettingsInput = Partial<SyslogSettings>;

export async function getSyslogSettings(): Promise<SyslogSettings> {
  const response = await authorizedFetch("/api/v1/settings/syslog");
  return response.json();
}

export async function updateSyslogSettings(
  input: SyslogSettingsInput,
): Promise<SyslogSettings> {
  const response = await authorizedFetch("/api/v1/settings/syslog", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return response.json();
}

export type SyslogEvent = {
  id: string;
  printer_id: string | null;
  printer_name: string | null;
  source_ip: string;
  received_at: string;
  device_timestamp: string | null;
  severity: SyslogSeverity | null;
  facility: number | null;
  hostname: string | null;
  app_name: string | null;
  message: string;
  raw: string;
};

export type SyslogEventPage = {
  items: SyslogEvent[];
  total: number;
  page: number;
  page_size: number;
};

export async function listPrinterSyslogEvents(
  printerId: string,
  params?: { severity?: SyslogSeverity; search?: string; page?: number; pageSize?: number },
): Promise<SyslogEventPage> {
  const query = new URLSearchParams();
  if (params?.severity) query.set("severity", params.severity);
  if (params?.search) query.set("search", params.search);
  if (params?.page) query.set("page", String(params.page));
  if (params?.pageSize) query.set("page_size", String(params.pageSize));
  const qs = query.toString();
  const response = await authorizedFetch(
    `/api/v1/printers/${printerId}/syslog${qs ? `?${qs}` : ""}`,
  );
  return response.json();
}

export async function listSyslogEvents(params?: {
  printerId?: string;
  severity?: SyslogSeverity;
  unmatchedOnly?: boolean;
  search?: string;
  since?: string;
  until?: string;
  page?: number;
  pageSize?: number;
}): Promise<SyslogEventPage> {
  const query = new URLSearchParams();
  if (params?.printerId) query.set("printer_id", params.printerId);
  if (params?.severity) query.set("severity", params.severity);
  if (params?.unmatchedOnly) query.set("unmatched_only", "true");
  if (params?.search) query.set("search", params.search);
  if (params?.since) query.set("since", params.since);
  if (params?.until) query.set("until", params.until);
  if (params?.page) query.set("page", String(params.page));
  if (params?.pageSize) query.set("page_size", String(params.pageSize));
  const qs = query.toString();
  const response = await authorizedFetch(`/api/v1/syslog${qs ? `?${qs}` : ""}`);
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
  // Only set for a follow-me job shown/released somewhere other than
  // where it was submitted — see HeldJobOut's docstring (app/schemas/release.py).
  printer_name: string | null;
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

export async function listHeldJobs(
  token: string,
  pin: string,
): Promise<HeldJob[]> {
  const response = await releaseFetch(
    `/api/v1/release/${encodeURIComponent(token)}/jobs`,
    { pin },
  );
  return response.json();
}

export async function releaseHeldJob(
  token: string,
  jobId: string,
  pin: string,
): Promise<HeldJob> {
  const response = await releaseFetch(
    `/api/v1/release/${encodeURIComponent(token)}/jobs/${jobId}/release`,
    { pin },
  );
  return response.json();
}

// --- Self-service web upload printing ---

export type SelfServicePrinter = {
  id: string;
  name: string;
  building: string | null;
  room: string | null;
  department: string | null;
  is_virtual: boolean;
};

export type SelfServicePrintResult = {
  printer_id: string;
  printer_name: string;
  filename: string;
  copies: number;
};

export async function listSelfServicePrinters(): Promise<SelfServicePrinter[]> {
  const response = await authorizedFetch("/api/v1/self-service-print/printers");
  return response.json();
}

export async function submitSelfServicePrint(
  printerId: string,
  file: File,
  copies: number,
): Promise<SelfServicePrintResult> {
  const formData = new FormData();
  formData.append("printer_id", printerId);
  formData.append("copies", String(copies));
  formData.append("file", file);
  const response = await authorizedFetch("/api/v1/self-service-print", {
    method: "POST",
    body: formData,
  });
  return response.json();
}

export type PrinterAllowedOu = {
  id: string;
  printer_id: string;
  ou_path: string;
};

export async function listPrinterAllowedOus(printerId: string): Promise<PrinterAllowedOu[]> {
  const response = await authorizedFetch(`/api/v1/printers/${printerId}/allowed-ous`);
  return response.json();
}

export async function createPrinterAllowedOu(
  printerId: string,
  ouPath: string,
): Promise<PrinterAllowedOu> {
  const response = await authorizedFetch(`/api/v1/printers/${printerId}/allowed-ous`, {
    method: "POST",
    body: JSON.stringify({ ou_path: ouPath }),
  });
  return response.json();
}

export async function deletePrinterAllowedOu(printerId: string, allowedId: string): Promise<void> {
  await authorizedFetch(`/api/v1/printers/${printerId}/allowed-ous/${allowedId}`, {
    method: "DELETE",
  });
}

export async function listAllGoogleWorkspaceOrgUnits(): Promise<string[]> {
  const response = await authorizedFetch("/api/v1/settings/google-workspace/org-units?scope=all");
  return response.json();
}
