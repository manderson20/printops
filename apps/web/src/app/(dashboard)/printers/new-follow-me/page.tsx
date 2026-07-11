"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createVirtualFollowMeQueue, ApiError } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

const initialForm = {
  name: "",
  building: "",
  room: "",
  department: "",
  notes: "",
};

export default function NewFollowMeQueuePage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [form, setForm] = useState(initialForm);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

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
      const printer = await createVirtualFollowMeQueue({
        name: form.name,
        building: form.building || null,
        room: form.room || null,
        department: form.department || null,
        notes: form.notes || null,
      });
      router.push(`/printers/${printer.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add Follow-Me queue");
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
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">
          Add Follow-Me Queue
        </h1>
        <p className="text-sm text-zinc-500">
          Creates a queue with no physical printer behind it — clients can select it just like a
          regular printer, but every job sent to it is held until the person releases it at
          whichever real printer they walk up to (any printer with Follow-Me enabled). It&apos;s
          created discoverable via AirPrint and with Follow-Me permanently on, since a queue like
          this only makes sense as one that&apos;s both.
        </p>

        <Field label="Name *">
          <Input
            value={form.name}
            onChange={(e) => update("name", e.target.value)}
            placeholder="Follow-Me Printing"
            required
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
            {submitting ? "Adding queue…" : "Add Follow-Me Queue"}
          </Button>
          <Button type="button" variant="secondary" onClick={() => router.push("/printers")}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  );
}
