"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  getGoogleSsoSettings,
  updateGoogleSsoSettings,
  type GoogleSsoSettings,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { PasswordField } from "@/components/ui/PasswordField";
import { ErrorState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

type LoadState =
  | { phase: "loading" }
  | { phase: "ok"; settings: GoogleSsoSettings }
  | { phase: "error"; message: string };

export default function GoogleSsoSettingsPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [form, setForm] = useState({
    client_id: "",
    client_secret: "",
    workspace_domain: "",
    redirect_base_url: "",
    initial_admin_emails: "",
  });
  const [enabled, setEnabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    getGoogleSsoSettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setForm((prev) => ({
          ...prev,
          client_id: settings.client_id ?? "",
          workspace_domain: settings.workspace_domain ?? "",
          redirect_base_url: settings.redirect_base_url ?? "",
          initial_admin_emails: settings.initial_admin_emails.join(", "),
        }));
        setEnabled(settings.enabled);
      })
      .catch((error: unknown) =>
        setState({
          phase: "error",
          message: error instanceof Error ? error.message : "Failed to load settings",
        }),
      );
  }, []);

  useEffect(() => {
    if (currentUser && currentUser.role !== "admin") {
      router.replace("/integrations");
    }
  }, [currentUser, router]);

  function update(field: keyof typeof form, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSave() {
    setSaving(true);
    setActionError(null);
    try {
      const settings = await updateGoogleSsoSettings({
        client_id: form.client_id || undefined,
        client_secret: form.client_secret || undefined,
        workspace_domain: form.workspace_domain || undefined,
        redirect_base_url: form.redirect_base_url || undefined,
        initial_admin_emails: form.initial_admin_emails
          .split(",")
          .map((email) => email.trim())
          .filter(Boolean),
        enabled,
      });
      setState({ phase: "ok", settings });
      setForm((prev) => ({ ...prev, client_secret: "" }));
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  if (state.phase === "loading" || currentUser === undefined || currentUser?.role !== "admin") {
    return <Spinner label="Loading settings…" />;
  }
  if (state.phase === "error") {
    return <ErrorState>{state.message}</ErrorState>;
  }

  const { settings } = state;

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Google Sign-In</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Lets staff sign into PrintOps with their Google Workspace account instead of the local
          admin/password fallback. New sign-ins default to Viewer unless their email is on the
          admin allowlist below — manage roles afterward on the Users page.
        </p>
      </div>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            1
          </span>
          <CardTitle className="mb-0">OAuth Client</CardTitle>
        </div>
        <p className="mb-4 ml-7 text-xs text-zinc-500">
          In Google Cloud Console: create an OAuth 2.0 Client ID of type{" "}
          <strong className="font-medium">Web application</strong> — separate from any service
          account used elsewhere. Add the Redirect Base URL below (with{" "}
          <code className="text-[11px]">/auth/google/callback</code> appended) as an authorized
          redirect URI.
        </p>
        <div className="ml-7 flex flex-col gap-4">
          <Field label="Client ID">
            <Input value={form.client_id} onChange={(e) => update("client_id", e.target.value)} />
          </Field>
          <PasswordField
            label={
              <>
                Client Secret{" "}
                {settings.has_client_secret && (
                  <span className="text-xs text-zinc-500">(already set — leave blank to keep)</span>
                )}
              </>
            }
            value={form.client_secret}
            onChange={(v) => update("client_secret", v)}
            placeholder={settings.has_client_secret ? "•••••••• (unchanged)" : ""}
          />
          <Field label="Redirect Base URL">
            <Input
              value={form.redirect_base_url}
              onChange={(e) => update("redirect_base_url", e.target.value)}
              placeholder="https://print.example.org"
            />
            <span className="text-xs text-zinc-500">
              Must exactly match what&apos;s registered in Google Cloud Console, including scheme —
              PrintOps appends <code className="text-[11px]">/auth/google/callback</code> itself.
            </span>
          </Field>
        </div>
      </Card>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            2
          </span>
          <CardTitle className="mb-0">Access Control</CardTitle>
        </div>
        <div className="ml-7 flex flex-col gap-4">
          <Field label="Workspace Domain">
            <Input
              value={form.workspace_domain}
              onChange={(e) => update("workspace_domain", e.target.value)}
              placeholder="example.org"
            />
            <span className="text-xs text-zinc-500">
              Only Google accounts in this Workspace domain can sign in — any other Google account
              is rejected.
            </span>
          </Field>
          <Field label="Initial Admin Emails">
            <Input
              value={form.initial_admin_emails}
              onChange={(e) => update("initial_admin_emails", e.target.value)}
              placeholder="you@example.org, other-admin@example.org"
            />
            <span className="text-xs text-zinc-500">
              Comma-separated. Anyone signing in with one of these addresses for the first time
              becomes an Admin; everyone else starts as Viewer.
            </span>
          </Field>
        </div>
      </Card>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            3
          </span>
          <CardTitle className="mb-0">Save</CardTitle>
        </div>
        <div className="ml-7 flex flex-col gap-4">
          <label className="flex items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              className="mt-1"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            <span>
              Enabled
              <br />
              <span className="text-xs text-zinc-500">
                Off = the &quot;Sign in with Google&quot; button on the login page won&apos;t work,
                even if configured above.
              </span>
            </span>
          </label>

          {actionError && <ErrorState>{actionError}</ErrorState>}
          <p className="text-xs text-zinc-500">
            There&apos;s no separate test step here — save, then try &quot;Sign in with Google&quot;
            from the login page (in another browser/incognito window if you&apos;re staying signed
            in here).
          </p>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </Card>

      <Card>
        <CardTitle className="mb-4">Status</CardTitle>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm text-zinc-600 dark:text-zinc-400">
          <dt className="font-medium text-zinc-500">Status</dt>
          <dd>
            {settings.enabled ? <Badge tone="success">Enabled</Badge> : <Badge tone="neutral">Disabled</Badge>}
          </dd>
          <dt className="font-medium text-zinc-500">Workspace Domain</dt>
          <dd>{settings.workspace_domain ?? "—"}</dd>
          <dt className="font-medium text-zinc-500">Initial Admins</dt>
          <dd>{settings.initial_admin_emails.join(", ") || "—"}</dd>
        </dl>
      </Card>
    </div>
  );
}
