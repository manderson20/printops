"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createPrinter, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { ErrorState } from "@/components/ui/EmptyState";

const initialForm = {
  name: "",
  ip_address: "",
  manufacturer: "",
  model: "",
  hostname: "",
  serial_number: "",
  building: "",
  room: "",
  department: "",
  notes: "",
};

export default function NewPrinterPage() {
  const router = useRouter();
  const [form, setForm] = useState(initialForm);
  const [airprintEnabled, setAirprintEnabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function update(field: keyof typeof initialForm, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const printer = await createPrinter({
        name: form.name,
        ip_address: form.ip_address,
        airprint_enabled: airprintEnabled,
        manufacturer: form.manufacturer || null,
        model: form.model || null,
        hostname: form.hostname || null,
        serial_number: form.serial_number || null,
        building: form.building || null,
        room: form.room || null,
        department: form.department || null,
        notes: form.notes || null,
      });
      router.push(`/printers/${printer.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add printer");
      setSubmitting(false);
    }
  }

  return (
    <div className="flex w-full max-w-lg flex-col">
      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-4 rounded-xl border border-black/[.08] bg-white p-8 dark:border-white/[.145] dark:bg-black"
      >
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Add Printer</h1>
        <p className="text-sm text-zinc-500">
          Enter the printer&apos;s name and IP address — PrintOps will probe it over IPP and
          fill in what it supports (duplex, color, staple, punch, etc.) automatically.
        </p>

        <Field label="Name *">
          <Input value={form.name} onChange={(e) => update("name", e.target.value)} required />
        </Field>

        <Field label="IP Address *">
          <Input
            value={form.ip_address}
            onChange={(e) => update("ip_address", e.target.value)}
            placeholder="10.0.1.25"
            required
          />
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Manufacturer">
            <Input value={form.manufacturer} onChange={(e) => update("manufacturer", e.target.value)} />
          </Field>
          <Field label="Model">
            <Input value={form.model} onChange={(e) => update("model", e.target.value)} />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Hostname">
            <Input value={form.hostname} onChange={(e) => update("hostname", e.target.value)} />
          </Field>
          <Field label="Serial Number">
            <Input
              value={form.serial_number}
              onChange={(e) => update("serial_number", e.target.value)}
            />
          </Field>
        </div>

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

        <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
          <input
            type="checkbox"
            className="mt-1"
            checked={airprintEnabled}
            onChange={(e) => setAirprintEnabled(e.target.checked)}
          />
          <span>
            Discoverable via AirPrint (Bonjour)
            <br />
            <span className="text-xs text-zinc-500">
              Off by default. When off, this printer won&apos;t show up automatically on
              Macs/iPads on the network — only devices explicitly configured to use it (e.g.
              via an MDM-pushed printer profile) can print to it. Turn on for general-use
              printers; leave off for anything handling confidential documents.
            </span>
          </span>
        </label>

        {error && <ErrorState>{error}</ErrorState>}

        <div className="mt-2 flex gap-3">
          <Button type="submit" disabled={submitting}>
            {submitting ? "Adding & probing printer…" : "Add Printer"}
          </Button>
          <Button type="button" variant="secondary" onClick={() => router.push("/printers")}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  );
}
