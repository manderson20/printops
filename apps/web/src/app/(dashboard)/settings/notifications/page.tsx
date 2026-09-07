"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  createNotificationChannel,
  deleteNotificationChannel,
  getNotificationSettings,
  listNotificationChannels,
  listNotificationEvents,
  testNotificationChannel,
  updateNotificationChannel,
  updateNotificationSettings,
  getSmtpSettings,
  testSmtpSettings,
  updateSmtpSettings,
  type NotificationChannel,
  type NotificationEvent,
  type NotificationSettings,
  type SmtpSettings,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState, SuccessState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

const TRIGGERS: { key: keyof NotificationSettings; label: string; hint: string }[] = [
  {
    key: "notify_printer_attention",
    label: "Printer needs attention",
    hint: "Jobs waiting for a printer that has never answered, or has been away a long time. Stays quiet for a printer switched off overnight.",
  },
  {
    key: "notify_printer_error",
    label: "Printer error",
    hint: "Jams, covers left open, empty trays — anything the printer itself reports as an error.",
  },
  {
    key: "notify_toner_low",
    label: "Low toner",
    hint: "A cartridge below the threshold. One message per cartridge, not one per check.",
  },
  {
    key: "notify_quota_exceeded",
    label: "Quota reached",
    hint: "Someone has hit their print quota and their jobs are being held. Off by default — it is the noisiest.",
  },
];

export default function NotificationSettingsPage() {
  const [settings, setSettings] = useState<NotificationSettings | null>(null);
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [events, setEvents] = useState<NotificationEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [newName, setNewName] = useState("");
  const [newTarget, setNewTarget] = useState("");
  const [newKind, setNewKind] = useState<"webhook" | "email">("webhook");
  const [busy, setBusy] = useState(false);

  const [smtp, setSmtp] = useState<SmtpSettings | null>(null);
  const [smtpPassword, setSmtpPassword] = useState("");
  const [smtpTestTo, setSmtpTestTo] = useState("");

  async function reload() {
    const [s, c, e, mail] = await Promise.all([
      getNotificationSettings(),
      listNotificationChannels(),
      listNotificationEvents(),
      getSmtpSettings(),
    ]);
    setSettings(s);
    setChannels(c);
    setEvents(e.events);
    setSmtp(mail);
  }

  useEffect(() => {
    // Guarded and awaited inside the effect rather than chained off it:
    // react-hooks flags a setState called synchronously from an effect body,
    // and the cancellation flag is the same idiom the Insights pages use for a
    // response that lands after the component has gone.
    let cancelled = false;
    (async () => {
      try {
        await reload();
      } catch (err: unknown) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load notification settings",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function patch(update: Partial<NotificationSettings>) {
    setError(null);
    try {
      setSettings(await updateNotificationSettings(update));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save");
    }
  }

  async function addChannel(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await createNotificationChannel(newName.trim(), newTarget.trim(), newKind);
      setNewName("");
      setNewTarget("");
      await reload();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not add that channel — check the URL.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function test(channel: NotificationChannel) {
    setError(null);
    setNotice(null);
    try {
      await testNotificationChannel(channel.id);
      setNotice(`Sent a test message to ${channel.name}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The test message did not get through.");
    }
    await reload().catch(() => undefined);
  }

  if (loading) return <Spinner />;
  if (!settings) return <ErrorState>{error ?? "Failed to load."}</ErrorState>;

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Notifications</h1>
        <p className="mt-1 text-sm text-zinc-500">
          PrintOps already notices when a printer stops answering, jams, or runs low on
          toner. This is where it tells somebody.
        </p>
      </div>

      {error && <ErrorState>{error}</ErrorState>}
      {notice && <SuccessState>{notice}</SuccessState>}

      <Card>
        <CardTitle className="mb-3">Where to send them</CardTitle>
        {channels.length === 0 ? (
          <EmptyState>
            No channels yet. Paste an incoming-webhook URL from Google Chat, Slack, Teams
            or Discord below — nothing is sent until there is somewhere to send it.
          </EmptyState>
        ) : (
          <ul className="mb-4 flex flex-col gap-2">
            {channels.map((channel) => (
              <li
                key={channel.id}
                className="flex flex-wrap items-center gap-3 rounded-lg border border-black/[.08] px-3 py-2 dark:border-white/[.145]"
              >
                <span className="font-medium">{channel.name}</span>
                <span className="font-mono text-xs text-zinc-500">{channel.target_hint}</span>
                {channel.enabled ? (
                  <Badge tone="info">on</Badge>
                ) : (
                  <Badge tone="neutral">off</Badge>
                )}
                {/* A channel that quietly stopped working is the failure nobody
                    notices, because the symptom is silence. */}
                {channel.last_error && <Badge tone="danger">failing</Badge>}
                <span className="ml-auto flex gap-2">
                  <Button variant="secondary" onClick={() => test(channel)}>
                    Test
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={async () => {
                      await updateNotificationChannel(channel.id, { enabled: !channel.enabled });
                      await reload();
                    }}
                  >
                    {channel.enabled ? "Disable" : "Enable"}
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={async () => {
                      await deleteNotificationChannel(channel.id);
                      await reload();
                    }}
                  >
                    Remove
                  </Button>
                </span>
                {channel.last_error && (
                  <p className="w-full text-xs text-red-600 dark:text-red-400">
                    {channel.last_error}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}

        <form className="flex flex-wrap items-end gap-2" onSubmit={addChannel}>
          <Field label="Kind" className="w-32">
            <select
              className="w-full rounded-lg border border-black/[.15] bg-white px-3 py-2 text-sm dark:border-white/[.2] dark:bg-black dark:text-zinc-50"
              value={newKind}
              onChange={(e) => setNewKind(e.target.value as "webhook" | "email")}
            >
              <option value="webhook">Chat</option>
              <option value="email">Email</option>
            </select>
          </Field>
          <Field label="Name" className="w-40">
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Helpdesk"
              required
            />
          </Field>
          <Field
            label={newKind === "email" ? "Address" : "Webhook URL"}
            className="min-w-0 flex-1"
          >
            <Input
              value={newTarget}
              onChange={(e) => setNewTarget(e.target.value)}
              placeholder={
                newKind === "email"
                  ? "helpdesk@district.org"
                  : "https://chat.googleapis.com/v1/spaces/..."
              }
              required
            />
          </Field>
          <Button type="submit" disabled={busy || !newName.trim() || !newTarget.trim()}>
            {busy ? "Adding…" : "Add"}
          </Button>
        </form>
        <p className="mt-2 text-xs text-zinc-500">
          A webhook URL is a credential — anyone holding it can post into that space — so
          it is stored write-only and never shown again. Email addresses are shown in
          full, because you need to see who is being copied.
        </p>
        {channels.some((c) => c.kind === "email") && !smtp?.enabled && (
          <p className="mt-2 text-sm text-amber-700 dark:text-amber-400">
            There is an email channel here but no relay configured below, so those
            messages cannot be sent.
          </p>
        )}
      </Card>

      <Card>
        <CardTitle className="mb-3">Email relay</CardTitle>
        <p className="mb-3 text-sm text-zinc-500">
          Needed only if you added an email channel above. Scheduled reports will use the
          same relay when they arrive.
        </p>
        {smtp && (
          <>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={smtp.enabled}
                onChange={async (e) => {
                  setSmtp(await updateSmtpSettings({ enabled: e.target.checked }));
                }}
              />
              Send through this relay
            </label>

            <div className="mt-4 flex flex-wrap gap-3">
              <Field label="Host" className="min-w-0 flex-1">
                <Input
                  value={smtp.host}
                  onChange={(e) => setSmtp({ ...smtp, host: e.target.value })}
                  onBlur={async () => setSmtp(await updateSmtpSettings({ host: smtp.host }))}
                  placeholder="smtp-relay.gmail.com"
                />
              </Field>
              <Field label="Port" className="w-24">
                <Input
                  type="number"
                  value={smtp.port}
                  onChange={(e) => setSmtp({ ...smtp, port: Number(e.target.value) })}
                  onBlur={async () => setSmtp(await updateSmtpSettings({ port: smtp.port }))}
                />
              </Field>
              <Field label="From" className="min-w-0 flex-1">
                <Input
                  value={smtp.from_address}
                  onChange={(e) => setSmtp({ ...smtp, from_address: e.target.value })}
                  onBlur={async () =>
                    setSmtp(await updateSmtpSettings({ from_address: smtp.from_address }))
                  }
                  placeholder="printops@district.org"
                />
              </Field>
            </div>

            <div className="mt-3 flex flex-wrap gap-3">
              <Field label="Username" className="min-w-0 flex-1">
                <Input
                  value={smtp.username}
                  onChange={(e) => setSmtp({ ...smtp, username: e.target.value })}
                  onBlur={async () =>
                    setSmtp(await updateSmtpSettings({ username: smtp.username }))
                  }
                />
              </Field>
              <Field label="Password" className="min-w-0 flex-1">
                <Input
                  type="password"
                  value={smtpPassword}
                  onChange={(e) => setSmtpPassword(e.target.value)}
                  placeholder={smtp.has_password ? "•••••••• (stored)" : "not set"}
                />
                {smtpPassword && (
                  <Button
                    variant="secondary"
                    className="mt-2"
                    onClick={async () => {
                      setSmtp(await updateSmtpSettings({ password: smtpPassword }));
                      setSmtpPassword("");
                    }}
                  >
                    Save password
                  </Button>
                )}
              </Field>
            </div>

            <label className="mt-3 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={smtp.use_starttls}
                onChange={async (e) =>
                  setSmtp(await updateSmtpSettings({ use_starttls: e.target.checked }))
                }
              />
              Use STARTTLS
            </label>

            <div className="mt-4 flex flex-wrap items-end gap-2">
              <Field label="Send a test to" className="min-w-0 flex-1">
                <Input
                  value={smtpTestTo}
                  onChange={(e) => setSmtpTestTo(e.target.value)}
                  placeholder="you@district.org"
                />
              </Field>
              <Button
                variant="secondary"
                disabled={!smtpTestTo.trim()}
                onClick={async () => {
                  setError(null);
                  setNotice(null);
                  try {
                    await testSmtpSettings(smtpTestTo.trim());
                    setNotice(`Sent a test message to ${smtpTestTo.trim()}.`);
                  } catch (err) {
                    setError(
                      err instanceof ApiError ? err.message : "The test message did not send.",
                    );
                  }
                }}
              >
                Send test
              </Button>
            </div>
          </>
        )}
      </Card>

      <Card>
        <CardTitle className="mb-3">What to send</CardTitle>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={settings.enabled}
            onChange={(e) => patch({ enabled: e.target.checked })}
          />
          Send notifications
        </label>

        <div className="mt-4 flex flex-col gap-3">
          {TRIGGERS.map((trigger) => (
            <label key={trigger.key} className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={Boolean(settings[trigger.key])}
                disabled={!settings.enabled}
                onChange={(e) => patch({ [trigger.key]: e.target.checked })}
              />
              <span>
                {trigger.label}
                <span className="block text-xs text-zinc-500">{trigger.hint}</span>
              </span>
            </label>
          ))}
        </div>

        <div className="mt-5 flex flex-wrap gap-4">
          <Field label="Wait before telling anyone (minutes)" className="w-64">
            <Input
              type="number"
              min={0}
              max={1440}
              value={settings.settle_minutes}
              onChange={(e) => patch({ settle_minutes: Number(e.target.value) })}
            />
            <p className="mt-1 text-xs text-zinc-500">
              A jam somebody clears in two minutes is not worth a message. Nothing is sent
              until the problem has lasted this long.
            </p>
          </Field>
          <Field label="Low toner below (%)" className="w-40">
            <Input
              type="number"
              min={1}
              max={99}
              value={settings.toner_low_percent}
              onChange={(e) => patch({ toner_low_percent: Number(e.target.value) })}
            />
          </Field>
        </div>
      </Card>

      <Card>
        <CardTitle className="mb-3">Recent</CardTitle>
        {events.length === 0 ? (
          <EmptyState>
            Nothing raised yet. That is the expected state — these only appear when
            something is wrong for longer than the wait above.
          </EmptyState>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {events.map((event) => (
              <li key={event.id} className="flex flex-wrap items-baseline gap-2">
                <span className="text-xs tabular-nums text-zinc-500">
                  {new Date(event.created_at).toLocaleString()}
                </span>
                <span>{event.title}</span>
                {event.delivered_at ? (
                  <Badge tone="info">sent</Badge>
                ) : event.gave_up ? (
                  <Badge tone="danger">gave up</Badge>
                ) : (
                  <Badge tone="warning">retrying</Badge>
                )}
                {event.last_error && (
                  <span className="w-full text-xs text-red-600 dark:text-red-400">
                    {event.last_error}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
