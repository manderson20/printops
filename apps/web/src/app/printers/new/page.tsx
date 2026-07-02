"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createPrinter, ApiError } from "@/lib/api";
import { useAuthGuard } from "@/lib/useAuthGuard";

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
  useAuthGuard();
  const router = useRouter();
  const [form, setForm] = useState(initialForm);
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

  const inputClass =
    "rounded border border-black/[.15] bg-transparent px-3 py-2 text-black dark:border-white/[.2] dark:text-zinc-50";
  const labelClass = "flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300";

  return (
    <div className="flex flex-1 justify-center bg-zinc-50 p-8 font-sans dark:bg-black">
      <form
        onSubmit={handleSubmit}
        className="flex w-full max-w-lg flex-col gap-4 rounded-xl border border-black/[.08] bg-white p-8 dark:border-white/[.145] dark:bg-black"
      >
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Add Printer</h1>
        <p className="text-sm text-zinc-500">
          Enter the printer&apos;s name and IP address — PrintOps will probe it over IPP and
          fill in what it supports (duplex, color, staple, punch, etc.) automatically.
        </p>

        <label className={labelClass}>
          Name *
          <input
            className={inputClass}
            value={form.name}
            onChange={(e) => update("name", e.target.value)}
            required
          />
        </label>

        <label className={labelClass}>
          IP Address *
          <input
            className={inputClass}
            value={form.ip_address}
            onChange={(e) => update("ip_address", e.target.value)}
            placeholder="10.0.1.25"
            required
          />
        </label>

        <div className="grid grid-cols-2 gap-4">
          <label className={labelClass}>
            Manufacturer
            <input
              className={inputClass}
              value={form.manufacturer}
              onChange={(e) => update("manufacturer", e.target.value)}
            />
          </label>
          <label className={labelClass}>
            Model
            <input
              className={inputClass}
              value={form.model}
              onChange={(e) => update("model", e.target.value)}
            />
          </label>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <label className={labelClass}>
            Hostname
            <input
              className={inputClass}
              value={form.hostname}
              onChange={(e) => update("hostname", e.target.value)}
            />
          </label>
          <label className={labelClass}>
            Serial Number
            <input
              className={inputClass}
              value={form.serial_number}
              onChange={(e) => update("serial_number", e.target.value)}
            />
          </label>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <label className={labelClass}>
            Building
            <input
              className={inputClass}
              value={form.building}
              onChange={(e) => update("building", e.target.value)}
            />
          </label>
          <label className={labelClass}>
            Room
            <input
              className={inputClass}
              value={form.room}
              onChange={(e) => update("room", e.target.value)}
            />
          </label>
          <label className={labelClass}>
            Department
            <input
              className={inputClass}
              value={form.department}
              onChange={(e) => update("department", e.target.value)}
            />
          </label>
        </div>

        <label className={labelClass}>
          Notes
          <textarea
            className={inputClass}
            value={form.notes}
            onChange={(e) => update("notes", e.target.value)}
            rows={2}
          />
        </label>

        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

        <div className="mt-2 flex gap-3">
          <button
            type="submit"
            disabled={submitting}
            className="rounded-full bg-foreground px-5 py-2 text-sm font-medium text-background transition-colors hover:bg-[#383838] disabled:opacity-50 dark:hover:bg-[#ccc]"
          >
            {submitting ? "Adding & probing printer…" : "Add Printer"}
          </button>
          <button
            type="button"
            onClick={() => router.push("/printers")}
            className="rounded-full border border-black/[.15] px-5 py-2 text-sm font-medium text-black dark:border-white/[.2] dark:text-zinc-50"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
