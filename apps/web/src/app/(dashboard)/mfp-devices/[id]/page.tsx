"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { getMfpDevice, type MfpDevice } from "@/lib/api";
import { CopierPanel } from "../../printers/[id]/CopierPanel";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

/** Copiers are no longer a place of their own — see ../page.tsx.
 *
 * A copier that belongs to a printer is managed on that printer's Copier tab,
 * so this route forwards there and old links keep working.
 *
 * A copier record with no printer linked is a different case. Nothing forwards
 * it anywhere, and with the copier list gone this URL is the only way back to
 * it, so it is rendered here in full rather than turned into a dead end. New
 * ones can no longer be made — a copier is created by switching one on for a
 * printer — but any that predate that still have to be manageable.
 */
export default function MfpDeviceRedirectPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const [state, setState] = useState<
    | { phase: "loading" }
    | { phase: "unlinked"; device: MfpDevice }
    | { phase: "error"; message: string }
  >({ phase: "loading" });

  useEffect(() => {
    let active = true;
    getMfpDevice(params.id)
      .then((device) => {
        if (!active) return;
        if (device.printer_id) {
          router.replace(`/printers/${device.printer_id}`);
        } else {
          setState({ phase: "unlinked", device });
        }
      })
      .catch(() =>
        active
          ? setState({ phase: "error", message: "That copier could not be found." })
          : undefined,
      );
    return () => {
      active = false;
    };
  }, [params.id, router]);

  if (state.phase === "error") {
    return <ErrorState>{state.message}</ErrorState>;
  }

  if (state.phase === "unlinked") {
    return (
      <div className="flex w-full flex-col gap-6">
        <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          This copier is not linked to a printer, so it has no machine page of
          its own. Its settings are below.
        </div>
        <CopierPanel deviceId={state.device.id} />
      </div>
    );
  }

  return <Spinner />;
}
