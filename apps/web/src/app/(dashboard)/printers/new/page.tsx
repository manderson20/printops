"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createPrinter, listPrinters, ApiError, type Printer } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

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

type DuplicateMatch = { printer: Printer; fields: string[] };

// Exact, case-insensitive match on any of these is a strong enough signal
// to warn about — an IP/hostname/serial can't legitimately belong to two
// distinct active printers, and a repeated name is very likely someone
// re-adding the same physical device rather than a coincidence. Only
// checked against active printers (listPrinters excludes archived by
// default) — an archived printer sharing an IP/name with a new one is the
// expected "replaced this device" pattern (see Printer.archived_at's
// docstring), not a duplicate.
function findDuplicates(form: typeof initialForm, existing: Printer[]): DuplicateMatch[] {
  const norm = (s: string) => s.trim().toLowerCase();
  const matches: DuplicateMatch[] = [];
  for (const printer of existing) {
    const fields: string[] = [];
    if (
      norm(form.ip_address) &&
      printer.ip_address &&
      norm(form.ip_address) === norm(printer.ip_address)
    ) {
      fields.push("IP address");
    }
    if (norm(form.hostname) && printer.hostname && norm(form.hostname) === norm(printer.hostname)) {
      fields.push("hostname");
    }
    if (
      norm(form.serial_number) &&
      printer.serial_number &&
      norm(form.serial_number) === norm(printer.serial_number)
    ) {
      fields.push("serial number");
    }
    if (norm(form.name) && norm(form.name) === norm(printer.name)) {
      fields.push("name");
    }
    if (fields.length > 0) matches.push({ printer, fields });
  }
  return matches;
}

export default function NewPrinterPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [form, setForm] = useState(initialForm);
  const [airprintEnabled, setAirprintEnabled] = useState(false);
  const [useTls, setUseTls] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [existingPrinters, setExistingPrinters] = useState<Printer[]>([]);

  useEffect(() => {
    listPrinters().then(setExistingPrinters).catch(() => setExistingPrinters([]));
  }, []);

  const duplicates = useMemo(
    () => findDuplicates(form, existingPrinters),
    [form, existingPrinters],
  );

  useEffect(() => {
    if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

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
        use_tls: useTls,
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

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="mx-auto flex w-full max-w-lg flex-col">
      {duplicates.length > 0 && !submitting && (
        // Fixed, not inline in the form — stays put in the viewport's top-right
        // corner as the admin scrolls through the rest of the fields, rather
        // than scrolling out of view right when it matters most (right before
        // they hit Add). Disappears once they actually submit.
        <div className="fixed right-4 top-4 z-50 w-80 max-w-[calc(100vw-2rem)] rounded-xl border border-red-900 bg-red-700 p-4 text-xs text-white shadow-lg dark:border-red-950 dark:bg-red-800 dark:text-red-50">
          <p className="text-base font-bold">Duplicate Printer Possible Match</p>
          <p className="mt-1 font-medium">This might already be a printer in the system</p>
          <ul className="mt-1 flex flex-col gap-1">
            {duplicates.map(({ printer, fields }) => (
              <li key={printer.id}>
                Matches{" "}
                <Link
                  href={`/printers/${printer.id}`}
                  target="_blank"
                  className="font-medium underline"
                >
                  {printer.name}
                </Link>{" "}
                on {fields.join(", ")}.
              </li>
            ))}
          </ul>
          <p className="mt-1">
            If this is genuinely a different physical device, go ahead and add it — this is just
            a heads-up, not a block.
          </p>
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-4 rounded-xl border border-black/[.08] bg-white p-8 dark:border-white/[.145] dark:bg-black"
      >
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Add Printer</h1>
          <WikiHelpLink page="Printers" anchor="adding-a-printer" />
        </div>
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

        <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
          <input
            type="checkbox"
            className="mt-1"
            checked={useTls}
            onChange={(e) => setUseTls(e.target.checked)}
          />
          <span>
            Connect via TLS (IPPS)
            <br />
            <span className="text-xs text-zinc-500">
              Off by default — most office printers use a self-signed certificate, so this
              encrypts traffic to the printer without strongly verifying its identity, and not
              every device handles IPPS cleanly. Only turn on if you&apos;ve confirmed this
              specific printer supports it (the Discovered Capabilities panel will show &quot;IPPS
              Supported&quot; after adding it if the device advertises it).
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
