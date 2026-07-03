"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  listHeldJobs,
  releaseHeldJob,
  ReleaseApiError,
  type HeldJob,
} from "@/lib/api";

const AUTO_RETURN_MS = 8000;

type Screen =
  | { view: "pin" }
  | { view: "jobs"; pin: string; jobs: HeldJob[] }
  | { view: "done"; message: string };

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
  onResolved: (pin: string, jobs: HeldJob[]) => void;
}) {
  const [pin, setPin] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (!pin) return;
    setSubmitting(true);
    setError(null);
    try {
      const jobs = await listHeldJobs(token, pin);
      if (jobs.length === 0) {
        setError("No held jobs found for that ID at this printer.");
        setPin("");
        return;
      }
      onResolved(pin, jobs);
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
  initialJobs,
  onDone,
}: {
  token: string;
  pin: string;
  initialJobs: HeldJob[];
  onDone: (message: string) => void;
}) {
  const [jobs, setJobs] = useState(initialJobs);
  const [releasingId, setReleasingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleRelease(jobId: string) {
    setReleasingId(jobId);
    setError(null);
    try {
      await releaseHeldJob(token, jobId, pin);
      const remaining = jobs.filter((job) => job.id !== jobId);
      setJobs(remaining);
      if (remaining.length === 0) {
        onDone("Job released — check the printer.");
      }
    } catch (err) {
      setError(err instanceof ReleaseApiError ? err.message : "Release failed.");
    } finally {
      setReleasingId(null);
    }
  }

  async function handleReleaseAll() {
    setError(null);
    for (const job of jobs) {
      setReleasingId(job.id);
      try {
        await releaseHeldJob(token, job.id, pin);
      } catch (err) {
        setError(err instanceof ReleaseApiError ? err.message : "Release failed.");
        setReleasingId(null);
        return;
      }
    }
    setReleasingId(null);
    onDone("All jobs released — check the printer.");
  }

  return (
    <div className="flex flex-1 flex-col items-center gap-6 px-6 py-10">
      <h1 className="text-xl font-semibold text-white">Your held jobs</h1>

      <div className="flex w-full max-w-md flex-col gap-3">
        {jobs.map((job) => (
          <div
            key={job.id}
            className="flex items-center justify-between gap-3 rounded-2xl border border-white/15 bg-white/5 px-4 py-3"
          >
            <div className="flex flex-col text-left">
              <span className="font-medium text-white">{job.document_name ?? "Untitled document"}</span>
              <span className="text-xs text-white/50">
                {new Date(job.created_at).toLocaleString()}
                {job.page_count ? ` · ${job.page_count} pages` : ""}
              </span>
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
          onResolved={(pin, jobs) => setScreen({ view: "jobs", pin, jobs })}
        />
      )}
      {screen.view === "jobs" && (
        <JobsScreen
          token={params.token}
          pin={screen.pin}
          initialJobs={screen.jobs}
          onDone={(message) => setScreen({ view: "done", message })}
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
