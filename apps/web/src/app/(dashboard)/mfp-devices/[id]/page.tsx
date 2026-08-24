"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { getMfpDevice } from "@/lib/api";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

/** A copier is part of its printer now, so this sends you to the printer.
 *
 * Kept as a redirect rather than deleted: these links are in people's
 * bookmarks and in the address bar of anyone who was on this page when the
 * change shipped, and a 404 would tell them their copier had been removed —
 * which, given what an admin knows about how much history hangs off a copier,
 * is an alarming thing to be told incorrectly.
 *
 * The lookup is needed because the URL knows the copier's id and not the
 * printer's.
 */
export default function CopierMoved() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMfpDevice(params.id)
      .then((device) => {
        if (device.printer_id) {
          router.replace(`/printers/${device.printer_id}`);
        } else {
          setError(
            "This copier is not linked to a printer, so it has no page to go to. " +
              "Link it to one from the printer's page, under Copier.",
          );
        }
      })
      .catch(() => setError("That copier could not be found."));
  }, [params.id, router]);

  if (error) return <ErrorState>{error}</ErrorState>;
  return <Spinner label="Taking you to this printer…" />;
}
