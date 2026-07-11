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

// Printer.manufacturer is freeform text (whatever the device/admin typed),
// not the constrained MfpVendor enum — best-effort keyword match so
// picking an existing printer also gets the vendor dropdown roughly
// right, rather than always resetting it to "generic".
function guessVendor(manufacturer: string | null): MfpVendor {
  const m = (manufacturer ?? "").toLowerCase();
  if (m.includes("konica") || m.includes("minolta")) return "konica_minolta";
  if (m.includes("canon")) return "canon";
  if (m.includes("hp") || m.includes("hewlett")) return "hp";
  if (m.includes("lexmark")) return "lexmark";
  if (m.includes("kyocera")) return "kyocera";
  if (m.includes("ricoh")) return "ricoh";
  if (m.includes("sharp")) return "sharp";
  if (m.includes("xerox")) return "xerox";
  return "generic";
}

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

  // The whole point of picking an existing printer here: this same
  // physical device's name/model/network/location were already typed in
  // once when it was added as a Printer — don't make the admin retype all
  // of it for the Copier record too. Fields stay editable afterward, so
  // picking the wrong printer (or wanting to tweak a value) isn't a dead
  // end.
  function handlePrinterSelect(id: string) {
    setPrinterId(id);
    const printer = printers.find((p) => p.id === id);
    if (!printer) return;
    setForm((prev) => ({
      ...prev,
      name: printer.name,
      model: printer.model ?? prev.model,
      serial_number: printer.serial_number ?? prev.serial_number,
      ip_address: printer.ip_address ?? prev.ip_address,
      hostname: printer.hostname ?? prev.hostname,
      building: printer.building ?? prev.building,
      room: printer.room ?? prev.room,
      department: printer.department ?? prev.department,
    }));
    setVendor(guessVendor(printer.manufacturer));
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
    <div className="mx-auto flex w-full max-w-lg flex-col">
      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-4 rounded-xl border border-black/[.08] bg-white p-8 dark:border-white/[.145] dark:bg-black"
      >
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Add MFP Device</h1>
        <p className="text-sm text-zinc-500">
          Track a walk-up copier for copy accounting — separate from adding it as a print queue
          (Printers). If this same physical device already has a CUPS queue, pick it below to fill
          in the fields below from it (still editable after) and link it so meter reads
          aren&apos;t duplicated.
        </p>

        <Field label="Existing Printer">
          <select
            value={printerId}
            onChange={(e) => handlePrinterSelect(e.target.value)}
            className="rounded border border-black/[.15] bg-transparent px-3 py-2 text-black dark:border-white/[.2] dark:text-zinc-50"
          >
            <option value="">None — type the details below manually</option>
            {printers.filter((p) => !p.is_virtual).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <span className="text-xs text-zinc-500">
            Picking a printer already set up here fills in its name/model/network/location below
            instead of retyping them, and links the two so meter reads reuse that printer&apos;s
            existing SNMP polling instead of polling twice.
          </span>
        </Field>

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
