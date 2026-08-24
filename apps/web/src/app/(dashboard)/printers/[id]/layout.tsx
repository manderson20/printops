"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import {
  ApiError,
  archivePrinter,
  deletePrinter,
  getPrinter,
  testPrintPrinter,
  unarchivePrinter,
  type Printer,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";
import { PrinterDetailContext } from "./PrinterDetailContext";

// Maps each detail tab to the matching heading anchor on the wiki's
// Printers page, so the help link always points at the section that
// documents whatever tab the admin is currently looking at.
const TAB_WIKI_ANCHORS: Record<string, string> = {
  "": "overview-tab",
  "/connection": "connection-tab",
  "/release": "release-and-quotas-tab",
  "/copier": "copier-tab",
  "/toner": "toner-tab",
  "/syslog": "syslog-tab",
  "/credentials": "credentials-tab",
  "/jobs": "jobs-tab",
};

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; printer: Printer }
  | { phase: "error"; message: string };

const ALL_TABS = [
  { href: "", label: "Overview" },
  { href: "/connection", label: "Connection" },
  { href: "/release", label: "Release & Quotas" },
  { href: "/copier", label: "Copier" },
  { href: "/toner", label: "Toner" },
  { href: "/syslog", label: "Syslog" },
  { href: "/credentials", label: "Credentials" },
  { href: "/jobs", label: "Jobs" },
] as const;

// A virtual Follow-Me queue has no real device — Toner/Syslog/Credentials
// are all meaningless for one (no cartridges, no device forwarding logs, no
// web login to store), and a queue that forwards to other printers has no
// glass of its own to copy from. Connection is kept: it's the MDM push info clients
// need to add this queue, which is the whole point of a virtual queue.
const VIRTUAL_HIDDEN_TABS = new Set([
  "/toner",
  "/syslog",
  "/credentials",
  "/copier",
]);

function tabsFor(printer: Printer) {
  return printer.is_virtual
    ? ALL_TABS.filter((tab) => !VIRTUAL_HIDDEN_TABS.has(tab.href))
    : ALL_TABS;
}

// The API appends its explanation after an em dash when a test page is held
// rather than printed — see _test_print_message in app/routers/printers.py.
function isHeldTestPrint(message: string) {
  return (
    message.includes("being held") || message.includes("holds jobs for release")
  );
}

export default function PrinterDetailLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useParams<{ id: string }>();
  const isAdmin = useCurrentUser()?.role === "admin";

  // "Does this machine actually print?" is the question an admin has on every
  // tab, not just Overview — it is as likely to be asked while looking at the
  // toner levels or the copier's counters as at the status card. So the button
  // lives in the tab bar, which is the one thing on screen no matter which tab
  // is open.
  const [testPrint, setTestPrint] = useState<
    | { phase: "idle" }
    | { phase: "sending" }
    | { phase: "done"; message: string }
    | { phase: "error"; message: string }
  >({ phase: "idle" });

  async function handleTestPrint(printerId: string) {
    setTestPrint({ phase: "sending" });
    try {
      const result = await testPrintPrinter(printerId);
      setTestPrint({ phase: "done", message: result.message });
    } catch (err) {
      setTestPrint({
        phase: "error",
        message: err instanceof ApiError ? err.message : "Test print failed",
      });
    }
  }
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
      <div className="mx-auto flex w-full max-w-6xl flex-col">
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold text-black dark:text-zinc-50">
              {state.printer.name}
            </h1>
            {state.printer.is_virtual && <Badge tone="neutral">Follow-Me Queue</Badge>}
            {state.printer.archived_at && <Badge tone="neutral">Archived</Badge>}
            <WikiHelpLink
              page="Printers"
              anchor={TAB_WIKI_ANCHORS[pathname.slice(basePath.length)] ?? "overview-tab"}
            />
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

          {/* Sits at the end of the tab row, pushed right, so it reads as an
              action on this machine rather than another place to navigate to.
              Hidden for a virtual Follow-Me queue: there is no device at the
              other end to put a page out. */}
          {!state.printer.is_virtual && (
            <div className="ml-auto flex shrink-0 items-center gap-2 pb-2 pl-4">
              {testPrint.phase === "done" && (
                <span
                  className={
                    isHeldTestPrint(testPrint.message)
                      ? "hidden text-xs text-amber-700 sm:inline dark:text-amber-400"
                      : "hidden text-xs text-emerald-700 sm:inline dark:text-emerald-400"
                  }
                >
                  {testPrint.message}
                </span>
              )}
              {testPrint.phase === "error" && (
                <span className="hidden text-xs text-red-700 sm:inline dark:text-red-400">
                  {testPrint.message}
                </span>
              )}
              <Button
                variant="secondary"
                onClick={() => handleTestPrint(state.printer.id)}
                disabled={testPrint.phase === "sending"}
              >
                {testPrint.phase === "sending" ? "Sending…" : "Print Test Page"}
              </Button>
            </div>
          )}
        </nav>

        {/* On a narrow screen the message cannot sit beside the button, so it
            gets its own line rather than being dropped. */}
        {(testPrint.phase === "done" || testPrint.phase === "error") && (
          <p
            className={`mb-4 text-xs sm:hidden ${
              testPrint.phase === "error"
                ? "text-red-700 dark:text-red-400"
                : isHeldTestPrint(testPrint.message)
                  ? "text-amber-700 dark:text-amber-400"
                  : "text-emerald-700 dark:text-emerald-400"
            }`}
          >
            {testPrint.message}
          </p>
        )}

        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </PrinterDetailContext.Provider>
  );
}
