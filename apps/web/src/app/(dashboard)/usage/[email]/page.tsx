"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { getUserUsage, listJobs, type Job, type UserUsage } from "@/lib/api";
import { formatBytes, formatCurrency } from "@/lib/format";
import { jobStatusInfo } from "@/lib/jobStatus";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

const JOBS_LIMIT = 200;

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; usage: UserUsage; jobs: Job[] }
  | { phase: "error"; message: string };

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-zinc-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-black dark:text-zinc-50">{value}</p>
    </Card>
  );
}

export default function UserUsageDetailPage() {
  const router = useRouter();
  const params = useParams<{ email: string }>();
  // decodeURIComponent on an already-decoded string is a safe no-op, so
  // this is correct regardless of whether this Next.js version hands
  // useParams() the raw URL segment or an already-decoded one — the row
  // link encodes the email once (it can contain "@"/"."), and without
  // this, a still-encoded param gets encoded a second time downstream
  // (getUserUsage, listJobs), turning "%40" into "%2540" and 404ing.
  const email = decodeURIComponent(params.email);
  const currentUser = useCurrentUser();
  const [state, setState] = useState<LoadState>({ phase: "loading" });

  useEffect(() => {
    if (currentUser === null) {
      router.replace("/login");
    } else if (currentUser && currentUser.role !== "admin") {
      router.replace("/printers");
    }
  }, [currentUser, router]);

  useEffect(() => {
    if (currentUser?.role !== "admin") return;
    Promise.all([
      getUserUsage(email),
      listJobs({ submitted_by: email, limit: JOBS_LIMIT }),
    ])
      .then(([usage, jobs]) => setState({ phase: "ok", usage, jobs }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load user usage",
        }),
      );
  }, [currentUser, email]);

  if (currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
      <div>
        <Link href="/usage" className="text-xs font-medium text-accent hover:underline">
          ← Back to Usage
        </Link>
      </div>

      {state.phase === "loading" && <Spinner label="Loading user usage…" />}
      {state.phase === "error" && <ErrorState>{state.message}</ErrorState>}

      {state.phase === "ok" && (
        <>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold text-black dark:text-zinc-50">
                {state.usage.name ?? state.usage.email}
              </h1>
              <WikiHelpLink page="Usage-Reports" />
            </div>
            {state.usage.name && (
              <p className="mt-1 text-sm text-zinc-500">{state.usage.email}</p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatCard label="Jobs" value={String(state.usage.job_count)} />
            <StatCard label="Total Pages" value={String(state.usage.total_pages)} />
            <StatCard label="Duplex / Simplex" value={`${state.usage.duplex_pages} / ${state.usage.simplex_pages}`} />
            <StatCard label="Mono / Color" value={`${state.usage.mono_pages} / ${state.usage.color_pages}`} />
            <StatCard label="Estimated Cost" value={formatCurrency(state.usage.estimated_cost)} />
            <StatCard label="Total Size" value={formatBytes(state.usage.total_bytes)} />
          </div>

          <Card className="overflow-hidden p-0">
            <div className="p-4">
              <CardTitle>Print Jobs</CardTitle>
            </div>
            {state.jobs.length === 0 ? (
              <div className="p-6 pt-0">
                <EmptyState>No jobs logged for this user yet.</EmptyState>
              </div>
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="bg-black/[.03] text-zinc-600 dark:bg-white/[.05] dark:text-zinc-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">Printer</th>
                    <th className="px-4 py-3 font-medium">Document</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Pages</th>
                    <th className="px-4 py-3 font-medium">Size</th>
                    <th className="px-4 py-3 font-medium">Submitted</th>
                  </tr>
                </thead>
                <tbody>
                  {state.jobs.map((job) => {
                    const info = jobStatusInfo(job.status);
                    return (
                      <tr
                        key={job.id}
                        className="border-t border-black/[.08] dark:border-white/[.1]"
                      >
                        <td className="px-4 py-3">
                          <Link
                            href={`/printers/${job.printer_id}`}
                            className="font-medium text-black hover:underline dark:text-zinc-50"
                          >
                            {job.printer_name}
                          </Link>
                        </td>
                        <td
                          className="max-w-[16rem] truncate px-4 py-3 text-zinc-600 dark:text-zinc-400"
                          title={job.document_name ?? undefined}
                        >
                          {job.document_name ?? "—"}
                        </td>
                        <td className="px-4 py-3">
                          <Badge tone={info.tone}>{info.label}</Badge>
                        </td>
                        <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                          {job.page_count ?? "—"}
                        </td>
                        <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                          {formatBytes(job.file_size_bytes)}
                        </td>
                        <td className="px-4 py-3 text-zinc-600 dark:text-zinc-400">
                          {new Date(job.created_at).toLocaleString()}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
