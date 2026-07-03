"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  getClassGuardSettings,
  getGoogleSsoSettings,
  getGoogleWorkspaceSettings,
  getMosyleSettings,
  type ClassGuardSettings,
  type GoogleSsoSettings,
  type GoogleWorkspaceSettings,
  type MosyleSettings,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";

function IntegrationCard({
  href,
  name,
  description,
  enabled,
}: {
  href: string;
  name: string;
  description: string;
  enabled: boolean | null;
}) {
  return (
    <Link href={href}>
      <Card className="transition-colors hover:bg-black/[.02] dark:hover:bg-white/[.03]">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-medium text-black dark:text-zinc-50">{name}</h2>
            <p className="mt-1 text-sm text-zinc-500">{description}</p>
          </div>
          {enabled === null ? (
            <Spinner />
          ) : enabled ? (
            <Badge tone="success">Enabled</Badge>
          ) : (
            <Badge tone="neutral">Not configured</Badge>
          )}
        </div>
      </Card>
    </Link>
  );
}

export default function IntegrationsPage() {
  const [mosyle, setMosyle] = useState<MosyleSettings | null>(null);
  const [classguard, setClassGuard] = useState<ClassGuardSettings | null>(null);
  const [googleWorkspace, setGoogleWorkspace] = useState<GoogleWorkspaceSettings | null>(null);
  const [googleSso, setGoogleSso] = useState<GoogleSsoSettings | null>(null);

  useEffect(() => {
    getMosyleSettings()
      .then(setMosyle)
      .catch(() => setMosyle(null));
    getClassGuardSettings()
      .then(setClassGuard)
      .catch(() => setClassGuard(null));
    getGoogleWorkspaceSettings()
      .then(setGoogleWorkspace)
      .catch(() => setGoogleWorkspace(null));
    getGoogleSsoSettings()
      .then(setGoogleSso)
      .catch(() => setGoogleSso(null));
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

      <IntegrationCard
        href="/integrations/google-sso"
        name="Google Sign-In"
        description="Lets staff log into PrintOps with their Google Workspace account, with role-based access."
        enabled={googleSso?.enabled ?? null}
      />
      <IntegrationCard
        href="/integrations/mosyle"
        name="Mosyle"
        description="MDM device→user lookup for print job attribution."
        enabled={mosyle?.enabled ?? null}
      />
      <IntegrationCard
        href="/integrations/google-workspace"
        name="Google Workspace"
        description="ChromeOS device→user lookup for print job attribution (tried after Mosyle)."
        enabled={googleWorkspace?.enabled ?? null}
      />
      <IntegrationCard
        href="/integrations/classguard"
        name="ClassGuard"
        description="DHCP lease lookup — resolves a print job's source IP to a MAC address for Mosyle/Google Workspace matching."
        enabled={classguard?.enabled ?? null}
      />
    </div>
  );
}
