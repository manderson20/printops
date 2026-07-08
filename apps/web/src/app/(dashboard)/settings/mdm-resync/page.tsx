"use client";

import { useState } from "react";
import { buildMdmResyncScript } from "@/lib/mdmResyncScript";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";

export default function MdmResyncSettingsPage() {
  const defaultHost = typeof window !== "undefined" ? window.location.hostname : "";
  const [host, setHost] = useState(defaultHost);
  const [copied, setCopied] = useState(false);

  const script = buildMdmResyncScript(host);

  function handleCopy() {
    navigator.clipboard.writeText(script).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="flex w-full max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">MDM Printer Resync</h1>
        <p className="mt-1 text-sm text-zinc-500">
          A Mac only checks a printer&apos;s capabilities (color support, paper sizes, etc.) once,
          when the printer is first added — it never re-verifies against the server afterward.
          If a printer&apos;s real capabilities change on this end (a fix like the one applied to
          MS - Cletus Copier, or a swapped-in replacement device), every Mac that already has it
          configured keeps showing stale options until something forces it to re-query.
        </p>
      </div>

      <Card>
        <CardTitle className="mb-1">How it works</CardTitle>
        <p className="mb-4 text-xs text-zinc-500">
          The script below re-probes each PrintOps-managed queue already configured on a Mac and
          refreshes its driver info in place — it never removes a queue, so the default printer,
          any app&apos;s saved printer preference, and jobs on other queues are all left alone. It
          skips a queue with a job pending right now, and does nothing at all if this PrintOps
          server isn&apos;t reachable when it runs. No credentials are embedded — it only talks
          IPP to the printer&apos;s own already-shared queue, the same thing printing to it does.
        </p>
        <Field label="This PrintOps server's hostname">
          <Input value={host} onChange={(e) => setHost(e.target.value)} placeholder="printops.example.org" />
          <span className="text-xs text-zinc-500">
            Prefilled from the address you&apos;re viewing this page at. Only change it if
            client Macs reach this server at a different hostname.
          </span>
        </Field>
      </Card>

      <Card>
        <div className="mb-1 flex items-center justify-between">
          <CardTitle className="mb-0">Script to push via Mosyle</CardTitle>
          <Button type="button" variant="secondary" onClick={handleCopy}>
            {copied ? "Copied!" : "Copy"}
          </Button>
        </div>
        <p className="mb-4 text-xs text-zinc-500">
          Paste this into a Mosyle Custom Command Profile (leave it running as root — the
          default — since <code className="text-[11px]">lpadmin</code> needs admin privileges).
          Use Mosyle&apos;s own scheduling to run it on whatever cadence you want; the script
          itself doesn&apos;t loop or self-schedule.
        </p>
        <pre className="overflow-x-auto rounded-lg border border-black/[.08] bg-zinc-100 p-3 text-[12px] leading-relaxed text-zinc-800 dark:border-white/[.145] dark:bg-white/[.06] dark:text-zinc-200">
          {script}
        </pre>
      </Card>
    </div>
  );
}
