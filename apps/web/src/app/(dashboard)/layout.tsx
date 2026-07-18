"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { getVersion } from "@/lib/api";
import { exitImpersonation, logout } from "@/lib/auth";
import { useAuthGuard } from "@/lib/useAuthGuard";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { useIdleSessionRefresh } from "@/lib/idleRefresh";

const ADMIN_ONLY_NAV_LINKS = [
  { href: "/printers", label: "Printers" },
  { href: "/jobs", label: "Jobs" },
  { href: "/syslog", label: "Syslog" },
] as const;

// Default role ("viewer") — everything else on the server is either
// admin-gated (Syslog, fleet-wide Jobs — see app/routers/syslog.py and
// app/routers/jobs.py's list_jobs) or scoped to just this person's own
// data (Insights — app/routers/reports.py's _report_filters). Print
// (self-service upload) is likewise scoped server-side to printers this
// user is allowed to target — app/self_service_print/service.py.
const VIEWER_NAV_LINKS = [
  { href: "/print", label: "Print" },
  { href: "/insights", label: "Insights" },
] as const;

// "ou_viewer" is read-only and scoped to Insights only — see
// app/routers/reports.py's _report_filters and app/models/user.py's
// granted_ou_paths docstring on the backend.
const OU_VIEWER_NAV_LINKS = [{ href: "/insights", label: "Insights" }] as const;

// Prefixes a non-admin role is allowed to navigate to directly (by URL,
// not just nav clicks) — everything else bounces to that role's default
// landing page, mirroring the pre-existing ou_viewer redirect below.
const ALLOWED_PATH_PREFIXES: Record<"viewer" | "ou_viewer", readonly string[]> = {
  viewer: ["/print", "/insights"],
  ou_viewer: ["/insights"],
};

export default function DashboardLayout({ children }: { children: ReactNode }) {
  useAuthGuard();
  useIdleSessionRefresh();
  const pathname = usePathname();
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [version, setVersion] = useState<string | null>(null);
  const navLinks =
    currentUser?.role === "admin"
      ? [
          { href: "/live", label: "Live Dashboard" },
          ...VIEWER_NAV_LINKS,
          ...ADMIN_ONLY_NAV_LINKS,
          { href: "/usage", label: "Usage" },
          { href: "/devices", label: "Devices" },
          { href: "/mfp-devices", label: "Copiers" },
          { href: "/quota-holds", label: "Quota Holds" },
          { href: "/settings", label: "Settings" },
          { href: "/updates", label: "Updates" },
        ]
      : currentUser?.role === "ou_viewer"
        ? OU_VIEWER_NAV_LINKS
        : VIEWER_NAV_LINKS;

  useEffect(() => {
    if (!currentUser) return;
    getVersion()
      .then(setVersion)
      .catch(() => setVersion(null));
  }, [currentUser]);

  // Non-admin roles are also restricted server-side (see the comments on
  // VIEWER_NAV_LINKS/OU_VIEWER_NAV_LINKS above), but this bounces a direct
  // URL hit to any other dashboard page back to that role's landing page
  // too, not just hides the nav links.
  useEffect(() => {
    if (!currentUser || currentUser.role === "admin") return;
    const allowedPrefixes = ALLOWED_PATH_PREFIXES[currentUser.role];
    const allowed = allowedPrefixes.some(
      (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
    );
    if (!allowed) {
      router.replace(allowedPrefixes[0]);
    }
  }, [currentUser, pathname, router]);

  function handleLogout() {
    logout();
    router.push("/login");
  }

  function handleExitImpersonation() {
    exitImpersonation();
    router.push("/settings/users");
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-zinc-50 font-sans print:h-auto print:overflow-visible dark:bg-black">
      {currentUser?.impersonated_by && (
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-amber-300 bg-amber-100 px-4 py-2 text-sm text-amber-900 print:hidden dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
          <span>
            Viewing as <strong>{currentUser.email ?? currentUser.username}</strong>{" "}
            ({currentUser.role}) — read-only. Nothing you do here affects real data.
          </span>
          <button
            onClick={handleExitImpersonation}
            className="shrink-0 rounded-full border border-amber-400 px-3 py-1 text-xs font-medium hover:bg-amber-200 dark:border-amber-800 dark:hover:bg-amber-900"
          >
            Exit
          </button>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">
        <aside className="flex w-56 shrink-0 flex-col overflow-y-auto border-r border-black/[.08] bg-white p-5 print:hidden dark:border-white/[.145] dark:bg-black">
          <Link
            href={currentUser?.role === "admin" ? "/live" : "/insights"}
            className="mb-8 flex items-center gap-2"
          >
            <Image src="/printops-logo.png" alt="" width={28} height={28} />
            <span className="text-base font-semibold text-black dark:text-zinc-50">
              PrintOps
            </span>
          </Link>

          <nav className="flex flex-1 flex-col gap-1">
            {navLinks.map((link) => {
              const active =
                pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                    active
                      ? "bg-accent text-accent-foreground"
                      : "text-zinc-600 hover:bg-black/[.04] dark:text-zinc-400 dark:hover:bg-white/[.06]"
                  }`}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>

          <button
            onClick={handleLogout}
            className="rounded-lg border border-black/[.15] px-3 py-2 text-sm font-medium text-black hover:bg-black/[.03] dark:border-white/[.2] dark:text-zinc-50 dark:hover:bg-white/[.05]"
          >
            Log out
          </button>
          {version && (
            <p className="mt-3 text-center text-xs text-zinc-400 dark:text-zinc-600">
              v{version}
            </p>
          )}
        </aside>

        <main className="flex flex-1 flex-col overflow-y-auto p-8 print:overflow-visible print:p-0">
          {children}
        </main>
      </div>
    </div>
  );
}
