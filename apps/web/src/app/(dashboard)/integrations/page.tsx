"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getMosyleSettings, type MosyleSettings } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";

export default function IntegrationsPage() {
  const [mosyle, setMosyle] = useState<MosyleSettings | null>(null);

  useEffect(() => {
    getMosyleSettings()
      .then(setMosyle)
      .catch(() => setMosyle(null));
  }, []);

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Integrations</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Third-party systems PrintOps connects to — device/user attribution, identity, and more
          as they're added.
        </p>
      </div>

      <Link href="/integrations/mosyle">
        <Card className="transition-colors hover:bg-black/[.02] dark:hover:bg-white/[.03]">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="font-medium text-black dark:text-zinc-50">Mosyle</h2>
              <p className="mt-1 text-sm text-zinc-500">
                MDM device→user lookup for print job attribution.
              </p>
            </div>
            {mosyle === null ? (
              <Spinner />
            ) : mosyle.enabled ? (
              <Badge tone="success">Enabled</Badge>
            ) : (
              <Badge tone="neutral">Not configured</Badge>
            )}
          </div>
        </Card>
      </Link>
    </div>
  );
}
