"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listPrinters, type Printer } from "@/lib/api";
import { capabilityBadges } from "@/lib/capabilities";
import { logout } from "@/lib/auth";
import { useRouter } from "next/navigation";
import { useAuthGuard } from "@/lib/useAuthGuard";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; printers: Printer[] }
  | { phase: "error"; message: string };

export default function PrintersPage() {
  useAuthGuard();
  const router = useRouter();
  const [state, setState] = useState<LoadState>({ phase: "loading" });

  useEffect(() => {
    listPrinters()
      .then((printers) => setState({ phase: "ok", printers }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load printers",
        }),
      );
  }, []);

  function handleLogout() {
    logout();
    router.push("/login");
  }

  return (
    <div className="flex flex-1 flex-col bg-zinc-50 p-8 font-sans dark:bg-black">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Printers</h1>
          <div className="flex gap-3">
            <Link
              href="/printers/new"
              className="rounded-full bg-foreground px-4 py-2 text-sm font-medium text-background hover:bg-[#383838] dark:hover:bg-[#ccc]"
            >
              Add Printer
            </Link>
            <button
              onClick={handleLogout}
              className="rounded-full border border-black/[.15] px-4 py-2 text-sm font-medium text-black dark:border-white/[.2] dark:text-zinc-50"
            >
              Log out
            </button>
          </div>
        </div>

        {state.phase === "loading" && <p className="text-zinc-500">Loading printers…</p>}
        {state.phase === "error" && (
          <p className="text-red-600 dark:text-red-400">{state.message}</p>
        )}
        {state.phase === "ok" && state.printers.length === 0 && (
          <p className="text-zinc-500">No printers yet. Add one to get started.</p>
        )}
        {state.phase === "ok" && state.printers.length > 0 && (
          <div className="overflow-hidden rounded-xl border border-black/[.08] dark:border-white/[.145]">
            <table className="w-full text-left text-sm">
              <thead className="bg-black/[.03] text-zinc-600 dark:bg-white/[.05] dark:text-zinc-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Name</th>
                  <th className="px-4 py-3 font-medium">Model</th>
                  <th className="px-4 py-3 font-medium">IP Address</th>
                  <th className="px-4 py-3 font-medium">Location</th>
                  <th className="px-4 py-3 font-medium">AirPrint</th>
                  <th className="px-4 py-3 font-medium">Capabilities</th>
                </tr>
              </thead>
              <tbody>
                {state.printers.map((printer) => (
                  <tr
                    key={printer.id}
                    className="border-t border-black/[.08] hover:bg-black/[.02] dark:border-white/[.1] dark:hover:bg-white/[.03]"
                  >
                    <td className="px-4 py-3">
                      <Link
                        href={`/printers/${printer.id}`}
                        className="font-medium text-black hover:underline dark:text-zinc-50"
                      >
                        {printer.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {printer.manufacturer ?? ""} {printer.model ?? "—"}
                      {printer.capabilities_error && (
                        <span className="ml-2 text-amber-700 dark:text-amber-400">
                          (capabilities not detected)
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {printer.ip_address}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {[printer.building, printer.room, printer.department]
                        .filter(Boolean)
                        .join(" / ") || "—"}
                    </td>
                    <td className="px-4 py-3">
                      {printer.airprint_enabled ? (
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                          Discoverable
                        </span>
                      ) : (
                        <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                          Hidden
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {capabilityBadges(printer.capabilities).map((badge) => (
                          <span
                            key={badge}
                            className="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-800 dark:bg-blue-950 dark:text-blue-300"
                          >
                            {badge}
                          </span>
                        ))}
                        {capabilityBadges(printer.capabilities).length === 0 && (
                          <span className="text-xs text-zinc-400">—</span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
