"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import {
  listHeldJobs,
  releaseHeldJob,
  ReleaseApiError,
  type HeldJob,
  type HeldJobsResult,
} from "@/lib/api";

const AUTO_RETURN_MS = 8000;

// A kiosk sits in a hallway, so the jobs screen cannot be left standing
// there with someone's name and document titles on it. Any touch restarts
// this; running out returns to the ID screen exactly as Done does. Long
// enough to read a list and think about it, short enough that the next
// person along finds the ID screen and not the last person's documents.
const IDLE_LOGOUT_MS = 60_000;

type Screen =
  | { view: "pin" }
  | {
      view: "jobs";
      pin: string;
      personName: string | null;
      jobs: HeldJob[];
    }
  | { view: "done"; message: string };

// What to call a job out loud. CUPS's number is the one an admin sees on
// the Jobs page, so it is the one worth reading down the phone to the
// office; a row CUPS hasn't numbered yet falls back to the head of its own
// id, which is at least unambiguous when there are two of them.
function jobRef(job: HeldJob): string {
  return job.cups_job_id !== null
    ? `Job ${job.cups_job_id}`
    : `Job ${job.id.slice(0, 8)}`;
}

function KeyPad({ onDigit, onBackspace, onSubmit, disabled }: {
  onDigit: (digit: string) => void;
  onBackspace: () => void;
  onSubmit: () => void;
  disabled: boolean;
}) {
  return (
    <div className="grid w-full max-w-xs grid-cols-3 gap-3">
      {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((digit) => (
        <button
          key={digit}
          type="button"
          disabled={disabled}
          onClick={() => onDigit(digit)}
          className="rounded-2xl border border-white/20 bg-white/5 py-6 text-2xl font-semibold text-white transition-colors hover:bg-white/10 disabled:opacity-50"
        >
          {digit}
        </button>
      ))}
      <button
        type="button"
        disabled={disabled}
        onClick={onBackspace}
        className="rounded-2xl border border-white/20 bg-white/5 py-6 text-lg font-medium text-white transition-colors hover:bg-white/10 disabled:opacity-50"
      >
        ⌫
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onDigit("0")}
        className="rounded-2xl border border-white/20 bg-white/5 py-6 text-2xl font-semibold text-white transition-colors hover:bg-white/10 disabled:opacity-50"
      >
        0
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={onSubmit}
        className="rounded-2xl bg-accent py-6 text-lg font-semibold text-accent-foreground transition-colors hover:bg-accent-hover disabled:opacity-50"
      >
        Go
      </button>
    </div>
  );
}

function PinScreen({
  token,
  onResolved,
}: {
  token: string;
  onResolved: (pin: string, result: HeldJobsResult) => void;
}) {
  const [pin, setPin] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (!pin) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await listHeldJobs(token, pin);
      if (result.jobs.length === 0) {
        setError("No held jobs found for that ID at this printer.");
        setPin("");
        return;
      }
      onResolved(pin, result);
    } catch (err) {
      setError(err instanceof ReleaseApiError ? err.message : "Something went wrong.");
      setPin("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-8 px-6 text-center">
      <div className="flex flex-col items-center gap-2">
        <h1 className="text-2xl font-semibold text-white">Enter your ID to release a print job</h1>
        <p className="text-sm text-white/60">Use the same ID number you use at the copier.</p>
      </div>

      <div className="flex h-14 w-full max-w-xs items-center justify-center rounded-2xl border border-white/20 bg-white/5 text-3xl tracking-[0.5em] text-white">
        {"•".repeat(pin.length) || <span className="text-white/30">— — — —</span>}
      </div>

      {error && <p className="max-w-xs text-sm text-red-400">{error}</p>}

      <KeyPad
        disabled={submitting}
        onDigit={(digit) => setPin((prev) => (prev.length < 12 ? prev + digit : prev))}
        onBackspace={() => setPin((prev) => prev.slice(0, -1))}
        onSubmit={handleSubmit}
      />
    </div>
  );
}

function JobsScreen({
  token,
  pin,
  personName,
  initialJobs,
  onDone,
  onLogOut,
}: {
  token: string;
  pin: string;
  personName: string | null;
  initialJobs: HeldJob[];
  onDone: (message: string) => void;
  onLogOut: () => void;
}) {
  const [jobs, setJobs] = useState(initialJobs);
  const [releasingId, setReleasingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [idleSince, setIdleSince] = useState(() => Date.now());

  // Set the moment this screen stops belonging to the person who unlocked
  // it — by Done, by the idle timer, or by unmounting. A release already
  // sent still completes at the printer; what must not happen is its
  // result landing on a screen the next person is now standing at, or
  // Release All carrying on through somebody else's queue.
  const finished = useRef(false);
  useEffect(() => () => {
    finished.current = true;
  }, []);

  function finish(leave: () => void) {
    finished.current = true;
    leave();
  }

  // Releasing counts as activity, so the timer restarts on every state
  // change that matters as well as on touch. It is deliberately not
  // cancelled while a release is in flight — that request either finishes
  // or fails long before a minute is out, and a kiosk that can get stuck
  // showing a name because one request hung is the failure this exists to
  // prevent.
  useEffect(() => {
    const timer = setTimeout(() => {
      finished.current = true;
      onLogOut();
    }, IDLE_LOGOUT_MS);
    return () => clearTimeout(timer);
  }, [idleSince, jobs, onLogOut]);

  async function handleRelease(jobId: string) {
    setReleasingId(jobId);
    setError(null);
    try {
      await releaseHeldJob(token, jobId, pin);
      if (finished.current) return;
      const remaining = jobs.filter((job) => job.id !== jobId);
      setJobs(remaining);
      if (remaining.length === 0) {
        onDone("Job released — check the printer.");
      }
    } catch (err) {
      if (finished.current) return;
      setError(err instanceof ReleaseApiError ? err.message : "Release failed.");
    } finally {
      if (!finished.current) setReleasingId(null);
    }
  }

  async function handleReleaseAll() {
    setError(null);
    for (const job of jobs) {
      // Checked before each one rather than only at the top: Release All
      // walks a queue one request at a time, and somebody pressing Done
      // partway through means the rest are not theirs to release. What has
      // already been sent still prints.
      if (finished.current) return;
      setReleasingId(job.id);
      try {
        await releaseHeldJob(token, job.id, pin);
      } catch (err) {
        if (finished.current) return;
        setError(err instanceof ReleaseApiError ? err.message : "Release failed.");
        setReleasingId(null);
        return;
      }
    }
    if (finished.current) return;
    setReleasingId(null);
    onDone("All jobs released — check the printer.");
  }

  return (
    <div
      className="flex flex-1 flex-col items-center gap-6 px-6 py-10"
      onPointerDown={() => setIdleSince(Date.now())}
    >
      <div className="flex w-full max-w-md items-start justify-between gap-4">
        <div className="flex flex-col text-left">
          <h1 className="text-xl font-semibold text-white">Your held jobs</h1>
          {/* The resolved name, not the ID that was typed: this is the
              check that catches a digit entered wrong before someone
              releases a stranger's documents. */}
          <p className="text-sm text-white/60">
            {personName ? `Signed in as ${personName}` : "Signed in"}
          </p>
        </div>
        {/* Release only what you came for and leave. Without this the
            screen stayed on this person until every one of their jobs had
            been released, which is not what someone wants when the rest
            are going to the office. */}
        <button
          type="button"
          onClick={() => finish(onLogOut)}
          className="shrink-0 rounded-full border border-white/20 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-white/10"
        >
          Done
        </button>
      </div>

      <div className="flex w-full max-w-md flex-col gap-3">
        {jobs.map((job) => (
          <div
            key={job.id}
            className="flex items-center justify-between gap-3 rounded-2xl border border-white/15 bg-white/5 px-4 py-3"
          >
            <div className="flex min-w-0 flex-col text-left">
              <span className="font-medium text-white">{job.document_name ?? "Untitled document"}</span>
              <span className="text-xs text-white/50">
                <span className="font-mono text-white/70">{jobRef(job)}</span>
                {" · "}
                {new Date(job.created_at).toLocaleString()}
                {job.page_count ? ` · ${job.page_count} pages` : ""}
              </span>
              {job.printer_name && (
                <span className="text-xs text-white/40">Sent to {job.printer_name}</span>
              )}
            </div>
            <button
              type="button"
              disabled={releasingId !== null}
              onClick={() => handleRelease(job.id)}
              className="shrink-0 rounded-full bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {releasingId === job.id ? "Releasing…" : "Release"}
            </button>
          </div>
        ))}
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {jobs.length > 1 && (
        <button
          type="button"
          disabled={releasingId !== null}
          onClick={handleReleaseAll}
          className="rounded-full border border-white/20 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-white/10 disabled:opacity-50"
        >
          Release All
        </button>
      )}
    </div>
  );
}

export default function ReleaseKioskPage() {
  const params = useParams<{ token: string }>();
  const [screen, setScreen] = useState<Screen>({ view: "pin" });

  useEffect(() => {
    if (screen.view !== "done") return;
    const timer = setTimeout(() => setScreen({ view: "pin" }), AUTO_RETURN_MS);
    return () => clearTimeout(timer);
  }, [screen]);

  return (
    <div className="flex min-h-screen flex-1 flex-col bg-zinc-950 font-sans select-none">
      {screen.view === "pin" && (
        <PinScreen
          token={params.token}
          onResolved={(pin, result) =>
            setScreen({
              view: "jobs",
              pin,
              personName: result.person_name,
              jobs: result.jobs,
            })
          }
        />
      )}
      {screen.view === "jobs" && (
        <JobsScreen
          token={params.token}
          pin={screen.pin}
          personName={screen.personName}
          initialJobs={screen.jobs}
          onDone={(message) => setScreen({ view: "done", message })}
          // Straight back to the ID screen rather than via the "done"
          // confirmation: nothing was released, so there is nothing to
          // confirm, and the next person should not have to wait out a
          // tick and a message that isn't about them. Dropping this state
          // is what forgets the PIN — see app/routers/release.py, there is
          // no server session to end.
          onLogOut={() => setScreen({ view: "pin" })}
        />
      )}
      {screen.view === "done" && (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <div className="text-4xl">✓</div>
          <p className="text-lg font-medium text-white">{screen.message}</p>
          <p className="text-sm text-white/50">Returning to the ID screen…</p>
        </div>
      )}
    </div>
  );
}
