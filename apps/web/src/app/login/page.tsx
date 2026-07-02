"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { login } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
      router.push("/printers");
    } catch {
      setError("Invalid username or password.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center bg-zinc-50 font-sans dark:bg-black">
      <form
        onSubmit={handleSubmit}
        className="flex w-full max-w-sm flex-col gap-4 rounded-xl border border-black/[.08] bg-white p-8 dark:border-white/[.145] dark:bg-black"
      >
        <h1 className="text-xl font-semibold text-black dark:text-zinc-50">Sign in to PrintOps</h1>

        <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
          Username
          <input
            className="rounded border border-black/[.15] bg-transparent px-3 py-2 text-black dark:border-white/[.2] dark:text-zinc-50"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
        </label>

        <label className="flex flex-col gap-1 text-sm text-zinc-700 dark:text-zinc-300">
          Password
          <input
            type="password"
            className="rounded border border-black/[.15] bg-transparent px-3 py-2 text-black dark:border-white/[.2] dark:text-zinc-50"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={submitting}
          className="mt-2 rounded-full bg-foreground px-5 py-2 text-sm font-medium text-background transition-colors hover:bg-[#383838] disabled:opacity-50 dark:hover:bg-[#ccc]"
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
