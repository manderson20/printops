"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useToken } from "@/lib/auth";
import { useCurrentUser } from "@/lib/useCurrentUser";

export default function Home() {
  const router = useRouter();
  const token = useToken();
  const currentUser = useCurrentUser();

  useEffect(() => {
    if (!token) {
      router.replace("/login");
      return;
    }
    // Live Dashboard's /live/hourly is admin-only (org-wide aggregate, not
    // self- or OU-scoped — see app/routers/reports.py's report_live_hourly
    // docstring), so non-admins land on Insights instead. Wait past
    // `undefined` (still loading) so a real admin doesn't get bounced
    // before /auth/me resolves.
    if (currentUser === undefined) return;
    router.replace(currentUser?.role === "admin" ? "/live" : "/insights");
  }, [router, token, currentUser]);

  return null;
}
