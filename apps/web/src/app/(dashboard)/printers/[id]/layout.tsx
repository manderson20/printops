"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import {
  ApiError,
  archivePrinter,
  deletePrinter,
  getPrinter,
  unarchivePrinter,
  type Printer,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { PrinterDetailContext } from "./PrinterDetailContext";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; printer: Printer }
  | { phase: "error"; message: string };

const ALL_TABS = [
  { href: "", label: "Overview" },
  { href: "/connection", label: "Connection" },
  { href: "/release", label: "Release & Quotas" },
  { href: "/toner", label: "Toner" },
  { href: "/syslog", label: "Syslog" },
  { href: "/credentials", label: "Credentials" },
  { href: "/jobs", label: "Jobs" },
] as const;

// A virtual Follow-Me queue has no real device — Toner/Syslog/Credentials
// are all meaningless for one (no cartridges, no device forwarding logs, no
// web login to store). Connection is kept: it's the MDM push info clients
// need to add this queue, which is the whole point of a virtual queue.
const VIRTUAL_HIDDEN_TABS = new Set(["/toner", "/syslog", "/credentials"]);

function tabsFor(printer: Printer) {
  return printer.is_virtual
    ? ALL_TABS.filter((tab) => !VIRTUAL_HIDDEN_TABS.has(tab.href))
    : ALL_TABS;
}

export default function PrinterDetailLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useParams<{ id: string }>();
  const isAdmin = useCurrentUser()?.role === "admin";
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [archiving, setArchiving] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);

  useEffect(() => {
    getPrinter(params.id)
      .then((printer) => setState({ phase: "ok", printer }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load printer",
        }),
      );
  }, [params.id]);

  async function handleDelete() {
    if (!confirm("Delete this printer?")) return;
    await deletePrinter(params.id);
    router.push("/printers");
  }

  async function handleArchive() {
    if (
      !confirm(
        "Archive this printer? It will stop accepting print jobs and be hidden from " +
          "AirPrint discovery, but its full job history stays intact. You can unarchive it " +
          "later if needed.",
      )
    )
      return;
    setArchiving(true);
    setArchiveError(null);
    try {
      const printer = await archivePrinter(params.id);
      setState({ phase: "ok", printer });
    } catch (err) {
      setArchiveError(err instanceof ApiError ? err.message : "Failed to archive printer");
    } finally {
      setArchiving(false);
    }
  }

  async function handleUnarchive() {
    setArchiving(true);
    setArchiveError(null);
    try {
      const printer = await unarchivePrinter(params.id);
      setState({ phase: "ok", printer });
    } catch (err) {
      setArchiveError(err instanceof ApiError ? err.message : "Failed to unarchive printer");
    } finally {
      setArchiving(false);
    }
  }

  if (state.phase === "loading") {
    return <Spinner label="Loading printer…" />;
  }
  if (state.phase === "error") {
    return <ErrorState>{state.message}</ErrorState>;
  }

  const basePath = `/printers/${params.id}`;

  return (
    <PrinterDetailContext.Provider
      value={{
        printer: state.printer,
        setPrinter: (printer) => setState({ phase: "ok", printer }),
      }}
    >
      <div className="mx-auto flex w-full max-w-2xl flex-col">
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold text-black dark:text-zinc-50">
              {state.printer.name}
            </h1>
            {state.printer.is_virtual && <Badge tone="neutral">Follow-Me Queue</Badge>}
            {state.printer.archived_at && <Badge tone="neutral">Archived</Badge>}
          </div>
          {isAdmin && (
            <div className="flex items-center gap-2">
              {state.printer.archived_at ? (
                <Button variant="secondary" onClick={handleUnarchive} disabled={archiving}>
                  {archiving ? "Unarchiving…" : "Unarchive"}
                </Button>
              ) : (
                <Button variant="secondary" onClick={handleArchive} disabled={archiving}>
                  {archiving ? "Archiving…" : "Archive"}
                </Button>
              )}
              <Button variant="danger" onClick={handleDelete}>
                Delete
              </Button>
            </div>
          )}
        </div>

        {state.printer.archived_at && (
          <div className="mb-6 rounded border border-black/[.08] bg-black/[.02] p-3 text-sm text-zinc-600 dark:border-white/[.1] dark:bg-white/[.03] dark:text-zinc-400">
            Archived {new Date(state.printer.archived_at).toLocaleString()} — no longer accepts
            print jobs or appears in AirPrint discovery. Its job history below is unaffected.
          </div>
        )}
        {archiveError && <ErrorState>{archiveError}</ErrorState>}

        {/* No flex `gap` touching this element — a gap immediately above/below
            a `sticky` flex child is a known Safari/iOS rendering glitch
            (scrolled-past content briefly shows through the gap before the
            sticky element's own background repaints over it). Explicit
            margins instead of gap avoid it entirely. */}
        <nav className="sticky top-0 z-10 mb-6 flex gap-1 overflow-x-auto border-b border-black/[.08] bg-zinc-50 pt-2 dark:border-white/[.145] dark:bg-black">
          {tabsFor(state.printer).map((tab) => {
            const href = `${basePath}${tab.href}`;
            const active = pathname === href;
            return (
              <Link
                key={tab.href}
                href={href}
                className={`shrink-0 whitespace-nowrap border-b-2 px-2 pb-3 text-sm font-medium transition-colors ${
                  active
                    ? "border-accent text-accent"
                    : "border-transparent text-zinc-600 hover:text-black dark:text-zinc-400 dark:hover:text-zinc-50"
                }`}
              >
                {tab.label}
              </Link>
            );
          })}
        </nav>

        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </PrinterDetailContext.Provider>
  );
}
