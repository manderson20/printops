"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Spinner } from "@/components/ui/Spinner";

const SETTINGS_NAV = [
  { href: "/settings/users", label: "Users" },
  { href: "/settings/snmp", label: "SNMP" },
  { href: "/settings/aliases", label: "Attribution Aliases" },
  { href: "/settings/insights", label: "Insights" },
] as const;

export default function SettingsLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const currentUser = useCurrentUser();

  useEffect(() => {
    if (currentUser === null) {
      router.replace("/login");
    } else if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="flex w-full max-w-5xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">
          Settings
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          Org-wide configuration that isn&apos;t tied to a single device or
          integration.
        </p>
      </div>

      <div className="flex gap-8">
        <nav className="flex w-44 shrink-0 flex-col gap-1">
          {SETTINGS_NAV.map((link) => {
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
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </div>
  );
}
