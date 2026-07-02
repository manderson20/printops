"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { getHealth, type HealthStatus } from "@/lib/api";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; data: HealthStatus }
  | { phase: "error"; message: string };

export default function Home() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });

  useEffect(() => {
    getHealth()
      .then((data) => setState({ phase: "ok", data }))
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Unknown error",
        }),
      );
  }, []);

  return (
    <div className="flex flex-1 items-center justify-center bg-zinc-50 font-sans dark:bg-black">
      <main className="flex w-full max-w-md flex-col items-center gap-6 rounded-xl border border-black/[.08] bg-white p-10 text-center dark:border-white/[.145] dark:bg-black">
        <Image
          src="/printops-logo.png"
          alt="PrintOps"
          width={160}
          height={160}
          priority
        />
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Enterprise print management platform — early scaffold.
        </p>

        {state.phase === "loading" && (
          <p className="text-zinc-500">Checking API status…</p>
        )}
        {state.phase === "ok" && (
          <p className="flex items-center gap-2 font-medium text-emerald-600 dark:text-emerald-400">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            API status: {state.data.status} ({state.data.service})
          </p>
        )}
        {state.phase === "error" && (
          <p className="flex items-center gap-2 font-medium text-red-600 dark:text-red-400">
            <span className="h-2 w-2 rounded-full bg-red-500" />
            Could not reach API: {state.message}
          </p>
        )}
      </main>
    </div>
  );
}
