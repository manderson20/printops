"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Merged into Settings > Users (OU grants are edited inline there now) —
// this redirect just covers anyone with the old URL bookmarked/open.
export default function PermissionsSettingsRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/settings/users");
  }, [router]);

  return null;
}
