"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  deleteCopierImportTemplate,
  listCopierImportTemplates,
  type CopierImportTemplate,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

export default function CopierImportTemplatesPage() {
  const isAdmin = useCurrentUser()?.role === "admin";
  const [templates, setTemplates] = useState<CopierImportTemplate[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    listCopierImportTemplates()
      .then(setTemplates)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load templates"));
  }

  useEffect(load, []);

  async function handleDelete(id: string) {
    if (!confirm("Delete this template?")) return;
    try {
      await deleteCopierImportTemplate(id);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete template");
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Import Mapping Templates</h1>
      <p className="text-sm text-zinc-500">
        Templates saved from a previous import&apos;s preview step (or created here) let a
        recurring vendor/model export skip re-mapping columns every period. Column mappings
        themselves are best created via the import wizard&apos;s &quot;save as template&quot;
        option, since that lets you confirm the mapping against a real file first.
      </p>

      {error && <ErrorState>{error}</ErrorState>}
      {templates === null && !error && <Spinner label="Loading templates…" />}
      {templates !== null && templates.length === 0 && (
        <EmptyState>No saved templates yet — create one from the import wizard.</EmptyState>
      )}
      {templates !== null && templates.length > 0 && (
        <div className="flex flex-col gap-3">
          {templates.map((template) => (
            <Card key={template.id}>
              <div className="flex items-start justify-between">
                <div>
                  <CardTitle>{template.name}</CardTitle>
                  <p className="mt-1 text-xs text-zinc-500">
                    {template.vendor}
                    {template.model && ` / ${template.model}`} · identity: {template.identity_type}
                  </p>
                  <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-zinc-600 dark:text-zinc-400">
                    {Object.entries(template.column_mapping).map(([field, column]) => (
                      <div key={field} className="contents">
                        <dt className="font-medium text-zinc-500">{field}</dt>
                        <dd>{column}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
                {isAdmin && (
                  <Button variant="danger" className="!px-3 !py-1 text-xs" onClick={() => handleDelete(template.id)}>
                    Delete
                  </Button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
