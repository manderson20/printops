"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ApiError,
  commitCopierImportBatch,
  listCopierImportTemplates,
  listMfpDevices,
  previewCopierImportBatch,
  uploadCopierImportFile,
  type CopierIdentityType,
  type CopierImportBatch,
  type CopierImportPreview,
  type CopierImportTemplate,
  type CopierImportUpload,
  type MfpDevice,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

const MAPPING_FIELDS: { field: string; label: string; required?: boolean }[] = [
  { field: "identity_value", label: "Identity (staff ID / PIN / badge / code)", required: true },
  { field: "occurred_at", label: "Timestamp (single event)" },
  { field: "period_start", label: "Period start" },
  { field: "period_end", label: "Period end" },
  { field: "activity_type", label: "Activity type (copy/scan/fax)" },
  { field: "page_count", label: "Page count" },
  { field: "sheet_count", label: "Sheet count" },
  { field: "color_page_count", label: "Color page count" },
  { field: "monochrome_page_count", label: "Monochrome page count" },
  { field: "duplex", label: "Duplex" },
  { field: "paper_size", label: "Paper size" },
  { field: "authentication_method", label: "Authentication method" },
];

const IDENTITY_TYPES: { value: CopierIdentityType; label: string }[] = [
  { value: "staff_id", label: "Staff ID" },
  { value: "pin", label: "PIN" },
  { value: "badge_id", label: "Badge / Card ID" },
  { value: "department_id", label: "Department ID" },
  { value: "user_code", label: "Vendor User Code" },
  { value: "vendor_user_id", label: "Vendor User ID" },
  { value: "email", label: "Email" },
];

type Step =
  | { phase: "select" }
  | { phase: "uploaded"; upload: CopierImportUpload }
  | { phase: "previewed"; upload: CopierImportUpload; preview: CopierImportPreview }
  | { phase: "committed"; batch: CopierImportBatch };

export default function NewCopierImportPage() {
  return (
    <Suspense fallback={<Spinner label="Loading…" />}>
      <NewCopierImportForm />
    </Suspense>
  );
}

function NewCopierImportForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentUser = useCurrentUser();
  const [devices, setDevices] = useState<MfpDevice[]>([]);
  const [templates, setTemplates] = useState<CopierImportTemplate[]>([]);
  const [deviceId, setDeviceId] = useState(searchParams.get("device_id") ?? "");
  const [templateId, setTemplateId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [identityType, setIdentityType] = useState<CopierIdentityType>("staff_id");
  const [periodLabel, setPeriodLabel] = useState("");
  const [saveTemplateName, setSaveTemplateName] = useState("");
  const [step, setStep] = useState<Step>({ phase: "select" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (currentUser && currentUser.role !== "admin") {
      router.replace("/copier-imports");
    }
  }, [currentUser, router]);

  useEffect(() => {
    listMfpDevices().then(setDevices).catch(() => setDevices([]));
    listCopierImportTemplates().then(setTemplates).catch(() => setTemplates([]));
  }, []);

  async function handleUpload() {
    if (!file || !deviceId) return;
    setBusy(true);
    setError(null);
    try {
      const upload = await uploadCopierImportFile(deviceId, file, templateId || undefined);
      setStep({ phase: "uploaded", upload });
      setMapping(upload.suggested_mapping ?? {});
      if (upload.suggested_identity_type) {
        setIdentityType(upload.suggested_identity_type as CopierIdentityType);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function handlePreview() {
    if (step.phase !== "uploaded") return;
    setBusy(true);
    setError(null);
    try {
      const preview = await previewCopierImportBatch(step.upload.batch_id, {
        column_mapping: mapping,
        identity_type: identityType,
        period_label: periodLabel || null,
        save_as_template: saveTemplateName
          ? {
              name: saveTemplateName,
              vendor: devices.find((d) => d.id === deviceId)?.vendor ?? "generic",
              column_mapping: mapping,
              identity_type: identityType,
            }
          : null,
      });
      setStep({ phase: "previewed", upload: step.upload, preview });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Preview failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleCommit() {
    if (step.phase !== "previewed") return;
    setBusy(true);
    setError(null);
    try {
      const batch = await commitCopierImportBatch(step.preview.batch_id, true);
      setStep({ phase: "committed", batch });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Commit failed");
    } finally {
      setBusy(false);
    }
  }

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <div className="flex items-center gap-2">
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">New Accounting Import</h1>
        <WikiHelpLink page="Copier-Accounting" anchor="imports" />
      </div>

      {step.phase === "select" && (
        <Card className="flex flex-col gap-4">
          <Field label="MFP Device *">
            <select
              value={deviceId}
              onChange={(e) => setDeviceId(e.target.value)}
              className="rounded border border-black/[.15] bg-transparent px-3 py-2 dark:border-white/[.2]"
            >
              <option value="">Select a device…</option>
              {devices.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.vendor})
                </option>
              ))}
            </select>
          </Field>

          <Field label="Mapping template (optional)">
            <select
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}
              className="rounded border border-black/[.15] bg-transparent px-3 py-2 dark:border-white/[.2]"
            >
              <option value="">None — map columns manually</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.vendor}
                  {t.model ? ` / ${t.model}` : ""})
                </option>
              ))}
            </select>
          </Field>

          <Field label="Accounting export file (CSV) *">
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="cursor-pointer text-sm file:mr-3 file:cursor-pointer file:rounded-full file:border-0 file:bg-accent file:px-4 file:py-2 file:text-sm file:font-medium file:text-accent-foreground file:transition-colors hover:file:bg-accent-hover"
            />
          </Field>

          {error && <ErrorState>{error}</ErrorState>}

          <Button onClick={handleUpload} disabled={busy || !file || !deviceId}>
            {busy ? "Uploading…" : "Upload"}
          </Button>
        </Card>
      )}

      {(step.phase === "uploaded" || step.phase === "previewed") && (
        <>
          <Card>
            <CardTitle className="mb-3">Map Columns</CardTitle>
            <p className="mb-3 text-xs text-zinc-500">
              Detected columns: {step.upload.header.join(", ")}
            </p>
            <div className="grid grid-cols-2 gap-3">
              {MAPPING_FIELDS.map(({ field, label, required }) => (
                <Field key={field} label={`${label}${required ? " *" : ""}`}>
                  <select
                    value={mapping[field] ?? ""}
                    onChange={(e) =>
                      setMapping((prev) => ({ ...prev, [field]: e.target.value }))
                    }
                    className="rounded border border-black/[.15] bg-transparent px-2 py-1 text-sm dark:border-white/[.2]"
                  >
                    <option value="">— not mapped —</option>
                    {step.upload.header.map((col) => (
                      <option key={col} value={col}>
                        {col}
                      </option>
                    ))}
                  </select>
                </Field>
              ))}
            </div>

            <div className="mt-4 grid grid-cols-2 gap-4">
              <Field label="Identity type *">
                <select
                  value={identityType}
                  onChange={(e) => setIdentityType(e.target.value as CopierIdentityType)}
                  className="rounded border border-black/[.15] bg-transparent px-2 py-1 text-sm dark:border-white/[.2]"
                >
                  {IDENTITY_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Period label (optional)">
                <Input
                  value={periodLabel}
                  onChange={(e) => setPeriodLabel(e.target.value)}
                  placeholder="e.g. 2026-06"
                />
              </Field>
            </div>

            <Field label="Save this mapping as a reusable template (optional)" className="mt-4">
              <Input
                value={saveTemplateName}
                onChange={(e) => setSaveTemplateName(e.target.value)}
                placeholder="Template name"
              />
            </Field>

            {error && <ErrorState>{error}</ErrorState>}

            <Button onClick={handlePreview} disabled={busy || !mapping.identity_value} className="mt-4">
              {busy ? "Previewing…" : "Preview"}
            </Button>
          </Card>

          {step.phase === "previewed" && (
            <Card>
              <CardTitle className="mb-3">Preview</CardTitle>
              <div className="mb-4 flex flex-wrap gap-2 text-sm">
                <Badge tone="neutral">{step.preview.total_rows} total</Badge>
                <Badge tone="success">{step.preview.valid_rows} valid</Badge>
                {step.preview.duplicate_rows > 0 && (
                  <Badge tone="warning">{step.preview.duplicate_rows} duplicate</Badge>
                )}
                {step.preview.unmapped_rows > 0 && (
                  <Badge tone="warning">{step.preview.unmapped_rows} unmapped identity</Badge>
                )}
                {step.preview.error_rows > 0 && (
                  <Badge tone="danger">{step.preview.error_rows} error</Badge>
                )}
              </div>

              <table className="w-full text-left text-xs">
                <thead className="text-zinc-500">
                  <tr>
                    <th className="py-1 font-medium">Row</th>
                    <th className="py-1 font-medium">Identity</th>
                    <th className="py-1 font-medium">Resolved Staff</th>
                    <th className="py-1 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {step.preview.sample_rows.map((row) => (
                    <tr key={row.row_number} className="border-t border-black/[.06] dark:border-white/[.08]">
                      <td className="py-1.5">{row.row_number}</td>
                      <td className="py-1.5 font-mono">{row.external_identity_used ?? "—"}</td>
                      <td className="py-1.5">
                        {row.staff_email ?? <span className="text-amber-700 dark:text-amber-400">Unmapped</span>}
                      </td>
                      <td className="py-1.5">
                        {row.error ? (
                          <Badge tone="danger">{row.error}</Badge>
                        ) : row.is_duplicate ? (
                          <Badge tone="warning">Duplicate</Badge>
                        ) : (
                          <Badge tone="success">OK</Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {error && <ErrorState>{error}</ErrorState>}

              <Button onClick={handleCommit} disabled={busy} className="mt-4">
                {busy ? "Committing…" : "Commit Import"}
              </Button>
            </Card>
          )}
        </>
      )}

      {step.phase === "committed" && (
        <Card>
          <CardTitle className="mb-3">Import Complete</CardTitle>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Imported {step.batch.imported_row_count} of {step.batch.row_count} rows
            {step.batch.unmapped_identity_count > 0 &&
              ` (${step.batch.unmapped_identity_count} with an unmapped identity — see Unmapped Activity)`}
            .
          </p>
          <div className="mt-4 flex gap-3">
            <Link href="/copier-imports">
              <Button variant="secondary">View Import History</Button>
            </Link>
            <Link href="/copier-unmapped">
              <Button variant="secondary">Unmapped Activity</Button>
            </Link>
            <Link href={`/mfp-devices/${step.batch.mfp_device_id}`}>
              <Button>Back to Device</Button>
            </Link>
          </div>
        </Card>
      )}
    </div>
  );
}
