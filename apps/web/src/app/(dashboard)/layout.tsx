"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode } from "react";
import { logout } from "@/lib/auth";
import { useAuthGuard } from "@/lib/useAuthGuard";

const NAV_LINKS = [
  { href: "/printers", label: "Printers" },
  { href: "/jobs", label: "Jobs" },
] as const;

export default function DashboardLayout({ children }: { children: ReactNode }) {
  useAuthGuard();
  const pathname = usePathname();
  const router = useRouter();

  function handleLogout() {
    logout();
    router.push("/login");
  }

  return (
    <div className="flex min-h-full flex-1 bg-zinc-50 font-sans dark:bg-black">
      <aside className="flex w-56 shrink-0 flex-col border-r border-black/[.08] bg-white p-5 dark:border-white/[.145] dark:bg-black">
        <Link href="/printers" className="mb-8 flex items-center gap-2">
          <Image src="/printops-logo.png" alt="" width={28} height={28} />
          <span className="text-base font-semibold text-black dark:text-zinc-50">PrintOps</span>
        </Link>

        <nav className="flex flex-1 flex-col gap-1">
          {NAV_LINKS.map((link) => {
            const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
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
      </aside>

      <main className="flex flex-1 flex-col overflow-y-auto p-8">{children}</main>
    </div>
  );
}
