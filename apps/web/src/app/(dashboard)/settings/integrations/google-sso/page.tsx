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
import { WikiHelpLink } from "@/components/ui/WikiHelpLink";

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
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    getGoogleSsoSettings()
      .then((settings) => {
        setState({ phase: "ok", settings });
        setForm((prev) => ({
          ...prev,
          client_id: settings.client_id ?? "",
          workspace_domain: settings.workspace_domain ?? "",
          redirect_base_url: settings.redirect_base_url ?? window.location.origin,
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
      router.replace("/settings/integrations");
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
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const redirectUri = `${form.redirect_base_url || origin || "https://your-printops-address"}/auth/google/callback`;

  function handleCopyRedirectUri() {
    navigator.clipboard.writeText(redirectUri).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Google Sign-In</h1>
          <WikiHelpLink page="Settings-Integrations" anchor="google-sign-in" />
        </div>
        <p className="mt-1 text-sm text-zinc-500">
          Lets staff sign into PrintOps with their Google Workspace account instead of the local
          admin/password fallback. New sign-ins default to Viewer unless their email is on the
          admin allowlist below — manage roles afterward on the Users page.
        </p>
      </div>

      <Card>
        <details className="group" open={!settings.client_id}>
          <summary className="cursor-pointer list-none">
            <div className="flex items-center justify-between">
              <CardTitle className="mb-0">Setup Guide — Start Here</CardTitle>
              <span className="text-xs text-zinc-500 group-open:hidden">Show</span>
              <span className="hidden text-xs text-zinc-500 group-open:inline">Hide</span>
            </div>
          </summary>
          <div className="mt-4 flex flex-col gap-5 text-sm text-zinc-600 dark:text-zinc-400">
            <p>
              You don&apos;t need to know anything about Google Cloud to do this — follow these
              steps in order, in a new browser tab, using the Google account your organization
              manages Google Workspace with (your IT admin account, if that&apos;s not you). It
              takes about five minutes and you only need to do it once.
            </p>

            <div>
              <p className="font-medium text-zinc-700 dark:text-zinc-300">
                Step 1 — Open Google Cloud Console
              </p>
              <p className="mt-1">
                Go to{" "}
                <a
                  href="https://console.cloud.google.com/"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent hover:underline"
                >
                  console.cloud.google.com
                </a>{" "}
                and sign in. If this is your first time here, Google may ask you to accept its
                terms — do that, then look for a project name at the top of the page next to the
                Google Cloud logo. If it says something like &quot;Select a project&quot;, click
                it, then click <strong className="font-medium">New Project</strong>, type any name
                (e.g. &quot;PrintOps&quot;), and click <strong className="font-medium">Create</strong>.
                Wait for it to finish, then make sure that project is selected.
              </p>
            </div>

            <div>
              <p className="font-medium text-zinc-700 dark:text-zinc-300">
                Step 2 — Tell Google this app exists
              </p>
              <p className="mt-1">
                In the menu on the left (click the ☰ icon if you don&apos;t see it), go to{" "}
                <strong className="font-medium">APIs &amp; Services → OAuth consent screen</strong>.
                This is where you tell Google what PrintOps is allowed to ask staff for when they
                sign in (just their name, email, and profile photo — nothing else).
              </p>
              <p className="mt-2">
                If it asks for a <strong className="font-medium">User type</strong>: pick{" "}
                <strong className="font-medium">Internal</strong> if your organization uses Google
                Workspace (this automatically limits sign-in to people in your organization) —
                otherwise pick <strong className="font-medium">External</strong>, and later look for
                a <strong className="font-medium">Publish App</strong> button and click it, or
                sign-in will only work for a hand-picked list of test accounts.
              </p>
              <p className="mt-2">
                Fill in an app name (e.g. &quot;PrintOps&quot;) and your email address where asked,
                then save/continue through the remaining pages using the defaults.
              </p>
            </div>

            <div>
              <p className="font-medium text-zinc-700 dark:text-zinc-300">
                Step 3 — Create the sign-in credentials
              </p>
              <p className="mt-1">
                Still under <strong className="font-medium">APIs &amp; Services</strong>, click{" "}
                <strong className="font-medium">Credentials</strong> in the left menu, then{" "}
                <strong className="font-medium">+ Create Credentials</strong> near the top, then{" "}
                <strong className="font-medium">OAuth client ID</strong>. For{" "}
                <strong className="font-medium">Application type</strong>, choose{" "}
                <strong className="font-medium">Web application</strong>.
              </p>
              <p className="mt-2">
                A form appears with two boxes for web addresses that look similar — this is the
                part people most often trip up on, so read carefully:
              </p>
              <ul className="mt-2 flex flex-col gap-3">
                <li>
                  <span className="font-medium text-zinc-700 dark:text-zinc-300">
                    &quot;Authorized JavaScript origins&quot;
                  </span>{" "}
                  — skip this one entirely, leave it blank.
                </li>
                <li>
                  <span className="font-medium text-zinc-700 dark:text-zinc-300">
                    &quot;Authorized redirect URIs&quot;
                  </span>{" "}
                  — click <strong className="font-medium">+ Add URI</strong> under this one and
                  paste in exactly the address below (click the button to copy it, so there&apos;s
                  no chance of a typo):
                  <div className="mt-2 flex items-center gap-2">
                    <code className="flex-1 overflow-x-auto rounded-lg bg-zinc-100 px-3 py-2 text-[12px] text-zinc-800 dark:bg-white/[.08] dark:text-zinc-200">
                      {redirectUri}
                    </code>
                    <Button type="button" variant="secondary" onClick={handleCopyRedirectUri}>
                      {copied ? "Copied!" : "Copy"}
                    </Button>
                  </div>
                </li>
              </ul>
              <p className="mt-2">
                Click <strong className="font-medium">Create</strong>. A window pops up showing a{" "}
                <strong className="font-medium">Client ID</strong> and{" "}
                <strong className="font-medium">Client secret</strong> — keep this window open for
                the next step.
              </p>
            </div>

            <div>
              <p className="font-medium text-zinc-700 dark:text-zinc-300">
                Step 4 — Bring the credentials back here
              </p>
              <p className="mt-1">
                Copy the <strong className="font-medium">Client ID</strong> and{" "}
                <strong className="font-medium">Client secret</strong> from that Google window into
                the matching boxes below on this page, fill in the Access Control section further
                down, turn on <strong className="font-medium">Enabled</strong>, and click{" "}
                <strong className="font-medium">Save</strong>.
              </p>
            </div>

            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
              <p className="font-medium">
                Getting &quot;Error 400: redirect_uri_mismatch&quot; when you try to sign in?
              </p>
              <p className="mt-1">
                That error comes from Google, before it even talks to PrintOps — it means the
                address pasted in Step 3 doesn&apos;t exactly match. Go back to that OAuth client
                in Google Cloud Console and check: it&apos;s listed under &quot;Authorized redirect
                URIs&quot; (not &quot;Authorized JavaScript origins&quot;), on the same client
                whose Client ID is in the box below, and matches the address above exactly — no
                extra slash at the end, no typos.
              </p>
            </div>
          </div>
        </details>
      </Card>

      <Card>
        <div className="mb-1 flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            1
          </span>
          <CardTitle className="mb-0">OAuth Client</CardTitle>
        </div>
        <p className="mb-4 ml-7 text-xs text-zinc-500">
          From the setup guide above: paste in the Client ID/Secret from the OAuth client you
          created, and confirm the Redirect Base URL matches what you registered in Google Cloud
          Console.
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
              placeholder="https://printops.example.org"
            />
            <span className="text-xs text-zinc-500">
              Prefilled from the address you&apos;re viewing this page at — only change it if
              that&apos;s not the domain staff will actually sign in from. Must exactly match
              what&apos;s registered in Google Cloud Console, including scheme — PrintOps appends{" "}
              <code className="text-[11px]">/auth/google/callback</code> itself.
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
