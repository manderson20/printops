"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  createMfpDevice,
  listConnectorTypes,
  listPrinters,
  type ConnectorTypeOption,
  type MfpVendor,
  type Printer,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

const VENDORS: MfpVendor[] = [
  "canon",
  "konica_minolta",
  "hp",
  "lexmark",
  "kyocera",
  "ricoh",
  "sharp",
  "xerox",
  "generic",
];

const initialForm = {
  name: "",
  model: "",
  serial_number: "",
  ip_address: "",
  hostname: "",
  building: "",
  room: "",
  department: "",
  notes: "",
};

export default function NewMfpDevicePage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [form, setForm] = useState(initialForm);
  const [vendor, setVendor] = useState<MfpVendor>("generic");
  const [connectorType, setConnectorType] = useState("generic_csv");
  const [connectorTypes, setConnectorTypes] = useState<ConnectorTypeOption[]>([]);
  const [printers, setPrinters] = useState<Printer[]>([]);
  const [printerId, setPrinterId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (currentUser && currentUser.role !== "admin") {
      router.replace("/mfp-devices");
    }
  }, [currentUser, router]);

  useEffect(() => {
    listConnectorTypes().then(setConnectorTypes).catch(() => setConnectorTypes([]));
    listPrinters().then(setPrinters).catch(() => setPrinters([]));
  }, []);

  function update(field: keyof typeof initialForm, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const device = await createMfpDevice({
        name: form.name,
        vendor,
        model: form.model || null,
        serial_number: form.serial_number || null,
        printer_id: printerId || null,
        ip_address: form.ip_address || null,
        hostname: form.hostname || null,
        building: form.building || null,
        room: form.room || null,
        department: form.department || null,
        connector_type: connectorType,
        notes: form.notes || null,
      });
      router.push(`/mfp-devices/${device.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add MFP device");
      setSubmitting(false);
    }
  }

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="flex w-full max-w-lg flex-col">
      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-4 rounded-xl border border-black/[.08] bg-white p-8 dark:border-white/[.145] dark:bg-black"
      >
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Add MFP Device</h1>
        <p className="text-sm text-zinc-500">
          Track a walk-up copier for copy accounting — separate from adding it as a print queue
          (Printers). If this same physical device already has a CUPS queue, link it below so
          meter reads aren&apos;t duplicated.
        </p>

        <Field label="Name *">
          <Input value={form.name} onChange={(e) => update("name", e.target.value)} required />
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Vendor">
            <select
              value={vendor}
              onChange={(e) => setVendor(e.target.value as MfpVendor)}
              className="rounded border border-black/[.15] bg-transparent px-3 py-2 text-black dark:border-white/[.2] dark:text-zinc-50"
            >
              {VENDORS.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Model">
            <Input value={form.model} onChange={(e) => update("model", e.target.value)} />
          </Field>
        </div>

        <Field label="Connector">
          <select
            value={connectorType}
            onChange={(e) => setConnectorType(e.target.value)}
            className="rounded border border-black/[.15] bg-transparent px-3 py-2 text-black dark:border-white/[.2] dark:text-zinc-50"
          >
            {connectorTypes.map((c) => (
              <option key={c.connector_type} value={c.connector_type}>
                {c.label}
              </option>
            ))}
          </select>
          <span className="text-xs text-zinc-500">
            Only connectors PrintOps actually implements are listed — a vendor without a real
            connector yet still works via Generic CSV Import.
          </span>
          {(() => {
            const notes = connectorTypes.find((c) => c.connector_type === connectorType)?.setup_notes;
            return notes ? (
              <div className="mt-2 rounded border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200">
                <p className="font-medium">Device setup</p>
                <p className="mt-1">{notes}</p>
              </div>
            ) : null;
          })()}
        </Field>

        <Field label="Linked Printer (optional)">
          <select
            value={printerId}
            onChange={(e) => setPrinterId(e.target.value)}
            className="rounded border border-black/[.15] bg-transparent px-3 py-2 text-black dark:border-white/[.2] dark:text-zinc-50"
          >
            <option value="">None — this device has no CUPS queue</option>
            {printers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <span className="text-xs text-zinc-500">
            Link only if this same physical device is already set up as a Printer — meter reads
            then reuse that printer&apos;s existing SNMP polling instead of polling twice.
          </span>
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="IP Address">
            <Input
              value={form.ip_address}
              onChange={(e) => update("ip_address", e.target.value)}
              placeholder="10.0.1.25"
            />
          </Field>
          <Field label="Hostname">
            <Input value={form.hostname} onChange={(e) => update("hostname", e.target.value)} />
          </Field>
        </div>

        <Field label="Serial Number">
          <Input
            value={form.serial_number}
            onChange={(e) => update("serial_number", e.target.value)}
          />
        </Field>

        <div className="grid grid-cols-3 gap-4">
          <Field label="Building">
            <Input value={form.building} onChange={(e) => update("building", e.target.value)} />
          </Field>
          <Field label="Room">
            <Input value={form.room} onChange={(e) => update("room", e.target.value)} />
          </Field>
          <Field label="Department">
            <Input value={form.department} onChange={(e) => update("department", e.target.value)} />
          </Field>
        </div>

        <Field label="Notes">
          <Textarea value={form.notes} onChange={(e) => update("notes", e.target.value)} rows={2} />
        </Field>

        {error && <ErrorState>{error}</ErrorState>}

        <div className="mt-2 flex gap-3">
          <Button type="submit" disabled={submitting}>
            {submitting ? "Adding…" : "Add MFP Device"}
          </Button>
          <Button type="button" variant="secondary" onClick={() => router.push("/mfp-devices")}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  );
}
