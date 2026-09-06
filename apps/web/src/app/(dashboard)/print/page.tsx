"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  listSelfServicePrinters,
  submitSelfServicePrint,
  type PrintFinishing,
  type PrintSides,
  type SelfServicePrinter,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState, SuccessState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; printers: SelfServicePrinter[] }
  | { phase: "error"; message: string };

function printerLabel(printer: SelfServicePrinter): string {
  const location = [printer.building, printer.room].filter(Boolean).join(" / ");
  return location ? `${printer.name} (${location})` : printer.name;
}

export default function SelfServicePrintPage() {
  const currentUser = useCurrentUser();
  const isImpersonating = Boolean(currentUser?.impersonated_by);
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [printerId, setPrinterId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [copies, setCopies] = useState("1");
  const [sides, setSides] = useState<PrintSides>("");
  const [finishings, setFinishings] = useState<PrintFinishing[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    listSelfServicePrinters()
      .then((printers) => {
        setState({ phase: "ok", printers });
        if (printers.length > 0) setPrinterId(printers[0].id);
      })
      .catch((err: unknown) =>
        setState({
          phase: "error",
          message: err instanceof Error ? err.message : "Failed to load printers",
        }),
      );
  }, []);

  const selected =
    state.phase === "ok" ? state.printers.find((p) => p.id === printerId) : undefined;

  // Options are per-printer, so switching printers has to clear them. Without
  // this, picking a machine with a stapler, ticking Staple, then switching to
  // one without would send a staple the server drops — the person would have
  // asked for something and been told it printed.
  const [optionsFor, setOptionsFor] = useState(printerId);
  if (printerId !== optionsFor) {
    setOptionsFor(printerId);
    setSides("");
    setFinishings([]);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!file || !printerId) return;
    setSubmitting(true);
    setError(null);
    setSuccessMessage(null);
    try {
      const result = await submitSelfServicePrint(printerId, file, Number(copies) || 1, {
        sides,
        finishings,
      });
      setSuccessMessage(`Sent "${result.filename}" to ${result.printer_name}.`);
      setFile(null);
      const input = document.getElementById("self-service-file-input") as HTMLInputElement | null;
      if (input) input.value = "";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to print");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-lg flex-col">
      <Card>
        <div className="mb-1 flex items-center gap-2">
          <CardTitle className="mb-0">Print</CardTitle>
          <WikiHelpLink page="Upload-and-Print" />
        </div>
        <p className="mb-4 text-xs text-zinc-500">
          Upload a PDF and pick a printer — no need to have it already set up on your own
          device. Only printers you&rsquo;re allowed to use are listed below.
        </p>

        {state.phase === "loading" && <Spinner label="Loading printers…" />}
        {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

        {state.phase === "ok" && state.printers.length === 0 && (
          <EmptyState>
            No printers are available to you yet — contact an admin if you think this is wrong.
          </EmptyState>
        )}

        {state.phase === "ok" && state.printers.length > 0 && (
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <Field label="Printer">
              <select
                value={printerId}
                onChange={(e) => setPrinterId(e.target.value)}
                className="rounded border border-black/[.15] bg-transparent px-3 py-2 text-sm text-black dark:border-white/[.2] dark:text-zinc-50"
              >
                {state.printers.map((printer) => (
                  <option key={printer.id} value={printer.id}>
                    {printerLabel(printer)}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Document (PDF)">
              <input
                id="self-service-file-input"
                type="file"
                accept="application/pdf"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="cursor-pointer text-sm text-zinc-700 file:mr-3 file:cursor-pointer file:rounded-full file:border-0 file:bg-accent file:px-4 file:py-2 file:text-sm file:font-medium file:text-accent-foreground file:transition-colors hover:file:bg-accent-hover dark:text-zinc-300"
              />
            </Field>

            <Field label="Copies" className="max-w-[8rem]">
              <Input
                type="number"
                min="1"
                value={copies}
                onChange={(e) => setCopies(e.target.value)}
              />
            </Field>

            {/* Only what the selected printer reported. A stapler checkbox on a
                machine with no stapler produces a job that quietly comes out
                unstapled, which is worse than no checkbox — the same shape of
                problem as a colour option on a monochrome printer. */}
            {(selected?.sides.length ?? 0) > 0 && (
              <Field label="Sides" className="max-w-[20rem]">
                <select
                  className="w-full rounded-lg border border-black/[.15] bg-white px-3 py-2 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
                  value={sides}
                  onChange={(e) => setSides(e.target.value as PrintSides)}
                >
                  {/* The default says what it does. Labelling it "One-sided"
                      while sending nothing meant a queue set to duplex printed
                      duplex under a form that read One-sided. */}
                  <option value="">Printer default</option>
                  <option value="one-sided">One-sided</option>
                  {selected?.sides.includes("two-sided-long-edge") && (
                    <option value="two-sided-long-edge">Double-sided</option>
                  )}
                  {selected?.sides.includes("two-sided-short-edge") && (
                    <option value="two-sided-short-edge">
                      Double-sided, flipped on the short edge
                    </option>
                  )}
                </select>
              </Field>
            )}

            {(selected?.finishings.length ?? 0) > 0 && (
              <Field label="Finishing">
                <div className="flex flex-wrap gap-4">
                  {selected?.finishings.map((finishing) => (
                    <label key={finishing} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={finishings.includes(finishing as PrintFinishing)}
                        onChange={(e) =>
                          setFinishings((current) =>
                            e.target.checked
                              ? [...current, finishing as PrintFinishing]
                              : current.filter((f) => f !== finishing),
                          )
                        }
                      />
                      {finishing === "staple" ? "Staple" : "Hole punch"}
                    </label>
                  ))}
                </div>
              </Field>
            )}

            {successMessage && <SuccessState>{successMessage}</SuccessState>}
            {error && <ErrorState>{error}</ErrorState>}
            {isImpersonating && (
              <p className="text-xs text-amber-700 dark:text-amber-400">
                You&rsquo;re viewing this as another user (read-only) — printing is disabled during
                a &quot;View as&quot; session.
              </p>
            )}

            <Button type="submit" disabled={submitting || !file || isImpersonating}>
              {submitting ? "Printing…" : "Print"}
            </Button>
          </form>
        )}
      </Card>
    </div>
  );
}
