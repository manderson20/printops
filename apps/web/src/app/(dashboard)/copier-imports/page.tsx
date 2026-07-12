"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { listCopierImportBatches, listMfpDevices, type CopierImportBatch, type MfpDevice } from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; batches: CopierImportBatch[] }
  | { phase: "error"; message: string };

const STATUS_TONE: Record<CopierImportBatch["status"], "neutral" | "info" | "success" | "danger"> = {
  uploaded: "neutral",
  previewed: "info",
  committed: "success",
  failed: "danger",
};

export default function CopierImportsPage() {
  return (
    <Suspense fallback={<Spinner label="Loading import history…" />}>
      <CopierImportsList />
    </Suspense>
  );
}

function CopierImportsList() {
  const isAdmin = useCurrentUser()?.role === "admin";
  const searchParams = useSearchParams();
  const deviceId = searchParams.get("device_id") ?? undefined;
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [devices, setDevices] = useState<Record<string, MfpDevice>>({});

  useEffect(() => {
    listCopierImportBatches(deviceId)
      .then((batches) => setState({ phase: "ok", batches }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load import history",
        }),
      );
    listMfpDevices()
      .then((list) => setDevices(Object.fromEntries(list.map((d) => [d.id, d]))))
      .catch(() => setDevices({}));
  }, [deviceId]);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Accounting Imports</h1>
          <WikiHelpLink page="Copier-Accounting" anchor="imports" />
        </div>
        {isAdmin && (
          <div className="flex gap-2">
            <Link href="/copier-imports/templates">
              <Button variant="secondary">Manage Templates</Button>
            </Link>
            <Link href={deviceId ? `/copier-imports/new?device_id=${deviceId}` : "/copier-imports/new"}>
              <Button>New Import</Button>
            </Link>
          </div>
        )}
      </div>

      <p className="text-sm text-zinc-500">
        Bring in a copier vendor&apos;s accounting export (CSV) and map it to staff copier
        identities. A committed import can&apos;t be deleted — its usage records feed the
        combined print+copy reports.
      </p>

      {state.phase === "loading" && <Spinner label="Loading import history…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}
      {state.phase === "ok" && state.batches.length === 0 && (
        <EmptyState>No accounting imports yet.</EmptyState>
      )}
      {state.phase === "ok" && state.batches.length > 0 && (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[800px] text-left text-sm">
              <thead className="bg-black/[.03] text-zinc-600 dark:bg-white/[.05] dark:text-zinc-400">
                <tr>
                  <th className="px-4 py-3 font-medium">File</th>
                  <th className="px-4 py-3 font-medium">Device</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Rows</th>
                  <th className="px-4 py-3 font-medium">Imported</th>
                  <th className="px-4 py-3 font-medium">Unmapped</th>
                  <th className="px-4 py-3 font-medium">Uploaded</th>
                </tr>
              </thead>
              <tbody>
                {state.batches.map((batch) => (
                  <tr
                    key={batch.id}
                    className="border-t border-black/[.08] dark:border-white/[.1]"
                  >
                    <td className="px-4 py-3 text-black dark:text-zinc-50">
                      {batch.status === "uploaded" || batch.status === "previewed" ? (
                        <Link
                          href={`/copier-imports/new?batch_id=${batch.id}`}
                          className="font-medium hover:underline"
                        >
                          {batch.original_filename}
                        </Link>
                      ) : (
                        batch.original_filename
                      )}
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {devices[batch.mfp_device_id]?.name ?? batch.mfp_device_id}
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={STATUS_TONE[batch.status]}>{batch.status}</Badge>
                    </td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">{batch.row_count}</td>
                    <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                      {batch.imported_row_count}
                    </td>
                    <td className="px-4 py-3">
                      {batch.unmapped_identity_count > 0 ? (
                        <Badge tone="warning">{batch.unmapped_identity_count}</Badge>
                      ) : (
                        <span className="text-zinc-400">0</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-500">
                      {new Date(batch.created_at).toLocaleString()} · {batch.uploaded_by}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
